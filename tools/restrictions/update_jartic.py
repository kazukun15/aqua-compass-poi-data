"""Import a small, non-live JARTIC K_2.1 pilot. Standard library; no device or user inputs."""

import argparse
import collections
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://www.jartic.or.jp/d/opendata"
SOURCE = "https://www.jartic.or.jp/service/opendata/"
LICENSE = BASE + "/riyou_kiyaku.pdf"
REGIONS = {"R33": "okayama", "R34": "hiroshima", "R38": "ehime"}
CODES = {"4", "5", "11", "12", "13"}
PAIR = re.compile(r"(\d{2,3}(?:\.\d{1,15})?)\s+(\d{2}(?:\.\d{1,15})?)", re.ASCII)
MAX_ZIP = 24 * 1024 * 1024
MAX_CSV = 200 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_TEXT = 16 * 1024 * 1024
FIELDS = {"拡張版標準フォーマット種別・バージョン", "都道府県コード", "共通規制種別コード", "点・線・面コード",
          "データ更新区分コード", "意思決定廃止日", "規制場所の経度緯度", "規制条件", "除外区間及び区域",
          "除外区間及び区域の経度緯度", "進入方向(座標)", "禁止する方向(座標)", "指定・禁止方向の別コード",
          "片側・両側コード"}
for number in range(1, 6):
    for head in ("対象期間", "規制時間", "除外期間", "除外時間"):
        FIELDS.update(f"{head}{number}_{end}" for end in ("開始", "終了"))
    FIELDS.update(f"{head}{number}" for head in ("規制曜日コード", "除外曜日コード"))
    FIELDS.update(f"{head}{number}_{group}" for head in ("対象車両コード", "除外車両コード") for group in "ABCD")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected redirect from the official data host")


def fetch(url, limit):
    if not url.startswith(BASE + "/"):
        raise ValueError("Unexpected JARTIC origin")
    request = urllib.request.Request(url, headers={"User-Agent": "AQUA-COMPASS-traffic-data/1.0"})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=35) as response:
        output = response.read(limit + 1)
    if len(output) > limit:
        raise ValueError("Official response exceeds configured size")
    return output


def official_index(index):
    items = [item for item in index if item.get("type") == "typeD"]
    if len(items) != 1:
        raise ValueError("Expected a single official Type D inventory")
    item = items[0]
    month = re.sub(r"[^0-9]", "", item["targetMonth"])
    released = re.sub(r"[^0-9]", "", item["releaseDay"])
    if not re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])", month) or not re.fullmatch(r"20\d{6}", released):
        raise ValueError("Invalid official dates")
    date = dt.datetime.strptime(released, "%Y%m%d").date()
    if month > released[:6]:
        raise ValueError("Target month after publication")
    links = {}
    releases = set()
    for region in item["targetList"]:
        prefecture = region["id"]
        if prefecture not in REGIONS:
            continue
        match = re.fullmatch(rf"/({released}\d{{4}})/typeD_{REGIONS[prefecture]}\.zip", region["link"])
        if prefecture in links or not match:
            raise ValueError("Unexpected regional Type D archive")
        releases.add(match[1])
        links[prefecture] = BASE + region["link"]
    if set(links) != set(REGIONS) or len(releases) != 1:
        raise ValueError("Incomplete or mixed official pilot edition")
    edition = releases.pop()
    dt.datetime.strptime(edition, "%Y%m%d%H%M")
    return edition, month, date.isoformat(), links


def coordinates(value, maximum=128):
    if not value:
        return ()
    fields = value.split(";")
    if len(fields) > maximum:
        return ()
    points = []
    for field in fields:
        match = PAIR.fullmatch(field.strip())
        if not match:
            return ()
        lon, lat = map(float, match.groups())
        if not 18 <= lat <= 46.5 or not 122 <= lon <= 155:
            return ()
        point = round(lat * 1e6), round(lon * 1e6)
        if not points or points[-1] != point:
            points.append(point)
    return tuple(points)


def clock(raw, end=False):
    if not re.fullmatch(r"[0-9]{1,4}", raw):
        return None
    hours, minutes = divmod(int(raw), 100)
    if (end and hours == 24 and minutes == 0) or (0 <= hours < 24 and 0 <= minutes < 60):
        return 60 * hours + minutes
    return None


def timetable(row):
    """Only plain daily windows targeting all vehicles or all motor vehicles, with no exceptions."""
    if any(v for k, v in row.items() if k.startswith(("除外", "対象期間", "規制曜日")) or k == "規制条件"):
        return "?"
    windows = []
    for n in range(1, 6):
        a, b = (row[f"規制時間{n}_{field}"] for field in ("開始", "終了"))
        types = tuple(row[f"対象車両コード{n}_{group}"] for group in "ABCD")
        if not any(types) and not (a or b):
            continue
        if types[0] not in ("1", "10") or any(types[1:]):
            return "?"
        if not a and not b:
            windows.append((0, 1440))
            continue
        start, end = clock(a), clock(b, True)
        if start is None or end is None or start == end:
            return "?"
        windows.append((start, end))
    # Blank scope is NOT all vehicles.
    if not windows:
        return "?"
    if (0, 1440) in windows:
        return "A"
    return ",".join(f"{a}-{b}" for a, b in sorted(set(windows)))


def normalize(row, region):
    row = {k: v.strip() for k, v in row.items() if k and isinstance(v, str)}
    code = row["共通規制種別コード"]
    if code not in CODES or row["データ更新区分コード"] == "3":
        return None
    if row["意思決定廃止日"] not in ("", "0000/00/00", "00000000"):
        return None
    if row["拡張版標準フォーマット種別・バージョン"] != "k_2.1" or row["都道府県コード"] != region[1:]:
        raise ValueError("Unknown Type D version or misplaced prefecture record")
    shape = row["点・線・面コード"]
    geometry = coordinates(row["規制場所の経度緯度"])
    if (shape == "1" and len(geometry) != 1 or shape == "2" and len(geometry) < 2 or
            shape == "3" and (len(geometry) < 4 or geometry[0] != geometry[-1]) or shape not in {"1", "2", "3"}):
        return None
    inbound = forbidden = ()
    use = timetable(row)
    if code == "11":
        direct = row["指定・禁止方向の別コード"]
        if direct == "2":
            geometry = geometry[::-1]
        if not (shape == "2" and direct in {"1", "2"}):
            use = "?"
    elif code == "12" and shape == "1":
        inbound = coordinates(row["進入方向(座標)"], 8)
        forbidden = coordinates(row["禁止する方向(座標)"], 8)
        # Do not infer disallowed turns as the complement of a potentially incomplete allowed list.
        if len(inbound) != 1 or len(forbidden) != 1:
            use = "?"
    else:
        # Other types are retained for reference; their approach/exception scope is not inferred.
        use = "?"
    def encode(points):
        return ";".join(f"{lat},{lon}" for lat, lon in points)
    return "|".join((region, code, shape, use, encode(geometry), encode(inbound), encode(forbidden)))


def read_archive(payload, region):
    seen, rows = collections.Counter(), set()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = archive.infolist()
        if len(files) > 100 or sum(info.file_size for info in files) > MAX_CSV:
            raise ValueError("Excessive Type D expansion")
        entries = [info for info in files if not info.is_dir() and info.filename.lower().endswith(".csv")]
        if not entries or any(info.flag_bits & 1 for info in entries):
            raise ValueError("Invalid Type D archives")
        for entry in entries:
            with archive.open(entry) as stream:
                records = csv.DictReader(io.TextIOWrapper(stream, encoding="cp932", newline=""))
                if not FIELDS.issubset(records.fieldnames or []) or len(set(records.fieldnames)) != len(records.fieldnames):
                    raise ValueError("Unexpected Type D CSV columns")
                for row in records:
                    if row.get("共通規制種別コード", "").strip() not in CODES:
                        continue
                    if None in row or any(row[name] is None for name in FIELDS):
                        raise ValueError("Truncated or surplus CSV columns on a target record")
                    seen[row["共通規制種別コード"].strip()] += 1
                    record = normalize(row, region)
                    if record:
                        rows.add(record)
    return sorted(rows), seen


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(data)
        except BaseException:
            stream.close()
            staged.unlink(missing_ok=True)
            raise
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def update(output, get=fetch):
    directory = output / "restrictions"
    manifest_path = directory / "manifest.json"
    old = None
    if manifest_path.exists():
        old = json.loads(manifest_path.read_bytes())
        edition = old.get("edition")
        if (not isinstance(edition, str) or not re.fullmatch(r"20[0-9]{10}", edition)
                or old.get("schemaVersion") != 1 or old.get("path") != f"restrictions/jartic-{edition}.gz"
                or not isinstance(old.get("counts"), dict) or set(old["counts"]) != set(REGIONS)
                or not all(type(n) is int and 1 <= n <= 100000 for n in old["counts"].values())):
            raise ValueError("Invalid installed pilot manifest")
        old_file = output / old["path"]
        if old_file.stat().st_size != old["sizeBytes"] or old_file.stat().st_size > MAX_OUTPUT \
                or digest(old_file.read_bytes()) != old["sha256"]:
            raise ValueError("Previous snapshot integrity check failed")
    official = get(BASE + "/opendata.json", 1024 * 1024)
    edition, month, released, urls = official_index(json.loads(official))
    if old and edition <= old["edition"]:
        if edition != old["edition"] or month != old["dataMonth"]:
            raise ValueError("Official edition would roll back")
        return old, False
    if old and month < old["dataMonth"]:
        raise ValueError("Official target month would roll back")
    records = []
    counts, regions = {}, {}
    for region in REGIONS:
        archive = get(urls[region], MAX_ZIP)
        region_rows, source_counts = read_archive(archive, region)
        counts[region] = len(region_rows)
        if not 100 <= counts[region] <= 30_000 or (old and counts[region] * 2 < old["counts"][region]):
            raise ValueError(f"Missing or unexpectedly changed volume for {region}")
        regions[region] = {"archiveUrl": urls[region], "archiveSha256": digest(archive),
                           "csvByType": dict(source_counts), "records": len(region_rows),
                           "simpleScheduleWithDirection": sum(r.split("|")[3] != "?" for r in region_rows)}
        records.extend(region_rows)
    # Recheck the listing after all archive downloads, avoiding a mixed or withdrawn release.
    if official_index(json.loads(get(BASE + "/opendata.json", 1024 * 1024))) != (edition, month, released, urls):
        raise ValueError("Official inventory changed during import")
    header = f"AQUA_RESTRICTIONS_V1|{edition}|{month}|{released}|{len(records)}"
    data = ("\n".join([header, *records]) + "\n").encode("ascii")
    payload = gzip.compress(data, mtime=0)
    if len(payload) > MAX_OUTPUT or len(data) > MAX_TEXT:
        raise ValueError("Generated snapshot exceeds app budget")
    path = f"restrictions/jartic-{edition}.gz"
    manifest = {"schemaVersion": 1, "edition": edition, "dataMonth": month, "releaseDate": released,
                "path": path, "sha256": digest(payload), "sizeBytes": len(payload), "counts": counts,
                "kind": "monthly_registered_road_restrictions_not_live", "sourceUrl": SOURCE, "licenseUrl": LICENSE}
    directory.mkdir(parents=True, exist_ok=True)
    candidate_path = output / path
    if candidate_path.exists() and candidate_path.read_bytes() != payload:
        raise ValueError("Edition files are immutable")
    atomic(candidate_path, payload)
    atomic(directory / f"metadata-{edition}.json", json_bytes({
        **manifest, "source": "JARTIC交通規制情報をAQUA COMPASSが抽出・加工", "regions": regions,
        "dataIndexSha256": digest(official), "retrievedDateJst": dt.datetime.now(
            dt.timezone(dt.timedelta(hours=9))).date().isoformat(),
    }))
    atomic(manifest_path, json_bytes(manifest))  # commit point: all inputs verified and version file present
    return manifest, True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result, changed = update(args.output)
    print(json.dumps({"changed": changed, "edition": result["edition"], "counts": result["counts"],
                      "fileBytes": result["sizeBytes"]}))
