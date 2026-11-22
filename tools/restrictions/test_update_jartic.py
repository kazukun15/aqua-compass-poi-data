import csv
import gzip
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from update_jartic import BASE, FIELDS, REGIONS, official_index, normalize, read_archive, timetable, update


def record(pref="R33", **updates):
    values = dict.fromkeys(FIELDS, "")
    values.update({
        "拡張版標準フォーマット種別・バージョン": "k_2.1", "都道府県コード": pref[1:],
        "共通規制種別コード": "11", "点・線・面コード": "2",
        "規制場所の経度緯度": "133.93 34.66;133.94 34.662", "指定・禁止方向の別コード": "1",
        "対象車両コード1_A": "10",
    })
    values.update(updates)
    return values


def listing(date="20261101", month="202609"):
    return [{"type": "typeD", "targetMonth": f"{month[:4]}年{month[4:]}月",
             "releaseDay": f"{date[:4]}年{date[4:6]}月{date[6:]}日", "targetList": [
                 {"id": key, "link": f"/{date}0000/typeD_{name}.zip"} for key, name in REGIONS.items()
             ]}]


def archive(pref, count):
    rows = io.StringIO(newline="")
    writer = csv.DictWriter(rows, sorted(FIELDS))
    writer.writeheader()
    for i in range(count):
        writer.writerow(record(pref, **{"規制場所の経度緯度": f"133.93 {34.66+i/100000:.5f};133.94 34.672"}))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        dest.writestr("official.csv", rows.getvalue().encode("cp932"))
    return out.getvalue()


def provider(index, count=150):
    _, _, _, urls = official_index(index)
    mapping = {url: archive(pref, count) for pref, url in urls.items()}
    return lambda url, limit: mapping[url] if url in mapping else json.dumps(index, ensure_ascii=False).encode()


class TestMonthlyRestrictions(unittest.TestCase):
    def test_schema_and_conversion_follow_directions(self):
        prohibited = normalize(record(), "R33").split("|")
        allowed = normalize(record(**{"指定・禁止方向の別コード": "2"}), "R33").split("|")
        self.assertEqual(allowed[4].split(";"), prohibited[4].split(";")[::-1])
        self.assertEqual("A", prohibited[3])
        self.assertEqual("?", normalize(record(**{"指定・禁止方向の別コード": ""}), "R33").split("|")[3])
        self.assertIsNone(normalize(record(**{"意思決定廃止日": "2026/09/01"}), "R33"))
        self.assertIsNone(normalize(record(**{"規制場所の経度緯度": "34.66 133.93"}), "R33"))
        with self.assertRaises(ValueError):
            read_archive(archive("R33", 1), "R34")

    def test_turn_requires_both_directions_not_complement(self):
        row = record(**{"共通規制種別コード": "12", "点・線・面コード": "1",
                        "規制場所の経度緯度": "133.93 34.66", "進入方向(座標)": "133.94 34.66",
                        "禁止する方向(座標)": "133.93 34.67",
                        "規制時間1_開始": "2200", "規制時間1_終了": "0700"})
        self.assertEqual("1320-420", normalize(row, "R33").split("|")[3])
        row["禁止する方向(座標)"] = ""
        self.assertEqual("?", normalize(row, "R33").split("|")[3])

    def test_unverified_exemptions_and_time_are_not_default_all_day(self):
        self.assertEqual("?", timetable(record(**{"対象車両コード1_A": "", "対象車両コード1_C": "1"})))
        self.assertEqual("?", timetable(record(**{"規制条件": "その他の条件"})))
        self.assertEqual("?", timetable(record(**{"除外曜日コード1": "2"})))
        self.assertEqual("?", timetable(record(**{"除外車両コード2_D": "1"})))
        self.assertEqual("?", timetable(record(**{"規制時間1_開始": "700"})))
        self.assertEqual("480-1020", timetable(record(**{"規制時間1_開始": "0800", "規制時間1_終了": "1700"})))
        self.assertEqual("A", timetable(record(**{"規制時間1_開始": "0000", "規制時間1_終了": "2400"})))

    def test_delayed_update_checks_only_index_and_next_edition_installs(self):
        with TemporaryDirectory() as root:
            path = Path(root)
            existing = listing()
            first, changed = update(path, provider(existing))
            self.assertTrue(changed)
            self.assertEqual("202611010000", first["edition"])
            snapshot = (path / first["path"]).read_bytes()
            self.assertTrue(gzip.decompress(snapshot).startswith(b"AQUA_RESTRICTIONS_V1|202611010000|"))
            def index_only(url, _limit):
                self.assertEqual(BASE + "/opendata.json", url)
                return json.dumps(existing).encode()
            self.assertEqual((first, False), update(path, index_only))
            newer, changed = update(path, provider(listing("20261204", "202610")))
            self.assertTrue(changed)
            self.assertEqual("202612040000", newer["edition"])
            self.assertEqual(snapshot, (path / first["path"]).read_bytes())

    def test_bad_regions_or_older_source_keep_previous_manifest(self):
        with TemporaryDirectory() as root:
            path = Path(root)
            update(path, provider(listing()))
            manifest = path / "restrictions/manifest.json"
            before = manifest.read_bytes()
            with self.assertRaises(ValueError):
                update(path, provider(listing("20261201", "202610"), count=80))
            with self.assertRaises(ValueError):
                update(path, provider(listing("20261001", "202608")))
            mixed = listing("20261201", "202610")
            mixed[0]["targetList"][0]["link"] = "/202611010000/typeD_okayama.zip"
            with self.assertRaises(ValueError):
                update(path, lambda *_: json.dumps(mixed).encode())
            self.assertEqual(before, manifest.read_bytes())


if __name__ == "__main__":
    unittest.main()
