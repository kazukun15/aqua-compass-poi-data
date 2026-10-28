# AQUA COMPASS 地域POIデータ

## 四国・中国地方抽出の部分収録

このリポジトリの地域POIは、[OpenStreetMap](https://www.openstreetmap.org/copyright)の公開データを基に、[Geofabrikの四国抽出](https://download.geofabrik.de/asia/japan/shikoku.html)と[中国地方抽出](https://download.geofabrik.de/asia/japan/chugoku.html)から作成したものです。両抽出データの基準時刻は2026年10月27日21:23:59 UTCです。地図データの著作権はOpenStreetMapの貢献者に帰属します。

**© OpenStreetMap contributors** — データは[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)に従って利用できます。[OSM Foundationの帰属ガイド](https://osmfoundation.org/wiki/Attribution_Guidelines)も参照してください。

`catalog.json`には各パッケージの版、SHA-256、サイズ、矩形およびGeofabrik公式抽出境界を記録しています。矩形や境界内の全施設を網羅するという意味ではありません。このデータは全国対応ではなく、元データの更新に追随する自動更新機構もまだありません。地域名や境界は現実の営業状況や最新性を保証しません。

両地域の重複表示を避けるため、OSM IDと全属性が四国側と一致する3施設は中国側から除外し、四国側に収録しています。中国側だけを取得した場合、この3施設は検索できないことがあります。

車両進入を禁止・制限する明示的なタグや、車以外の専用と判断できる駐車場を保守的に除外しています。ただし、元データが不足している施設もあります。名称やアクセスが不明の場合は不明のままです。施設座標は近似位置であり、入口、敷地内の地点、駐車場の空き、営業時間、車両での進入可否を保証しません。実際の標識や現地の状況に従ってください。

パッケージはOSM由来の公開施設データを限定カテゴリに変換したものです。走行履歴、目的地履歴、APIキーやアプリのソースコードは含みません。作成元のGeofabrik公開抽出には投稿者名・ID・変更セットIDが含まれません。`verification-*.json`には件数や出典を記録します。
