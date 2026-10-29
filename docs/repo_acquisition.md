# リポジトリの入手経路（再現手順）

`repos/` の 960 ディレクトリがどこから来たのかを、後から同じ状態に戻せる形で記録する。
機械可読な一覧は **`out/repo_manifest.csv`**（1 行 = 1 ディレクトリ）。

```bash
python3 scripts/13_manifest.py          # 960 行を再生成
column -s, -t out/repo_manifest.csv | less -S
```

| 列 | 意味 |
|---|---|
| `repo` | ディレクトリ名。`out/*.csv` の `repo` (`owner/name`) を `__` で連結したもの |
| `kind` | `github` / `swh` / `mirror` / `symlink` |
| `source` | クローン元 URL（`swh` は "swh vault"） |
| `swhid` | SWH のスナップショット ID（`kind=swh` のみ。再取得にこれを使う） |
| `head` | 復元した HEAD の完全 SHA。再現できたかの照合に使う |
| `swh_status` / `swh_visit` | `work/swh_state.jsonl` の最終 status とスナップショット日時 |

現状の内訳: **github 875 / swh 62 / mirror 19 / symlink 4**。

---

## 1. GitHub からの直接クローン（875 件）

`scripts/02_clone_repos.py` が `work/repo_meta.json` の存命リポジトリを取得する。
全履歴を持ち、作業ツリーは Java だけ、という形。

```bash
git clone --filter=blob:none --no-checkout --single-branch https://github.com/<owner>/<name>.git repos/<owner>__<name>
git -C repos/<owner>__<name> sparse-checkout init --no-cone
git -C repos/<owner>__<name> sparse-checkout set --no-cone '*.java'
git -C repos/<owner>__<name> checkout HEAD
```

- 進捗は `work/clone_state.jsonl`（追記・再開可能）。**875 行 = マニフェストの `kind=github` と一致**。
- `git blame` / `git log -S` をオフラインで回すため、`scripts/11_backfill_all.py` で全 blob を後から取得済み。
- 認証は `common.git_env()` が `url.https://x-access-token:<token>@github.com/.insteadOf` を注入
  （匿名の部分クローンは数百リクエストで HTTP 400 になるため）。

```bash
python3 scripts/02_clone_repos.py          # 未取得分だけ取得。冪等
```

## 2. Software Heritage の Vault からの復元（62 件）

GitHub 側で 404（削除・非公開）になったリポジトリを、SWH のスナップショットから戻す。

```bash
# 1) origin の visit 一覧から最新の full git visit を選ぶ
GET https://archive.softwareheritage.org/api/1/origin/https://github.com/<owner>/<name>/visits/
# 2) スナップショットの調理を依頼（git-bare = 履歴付き bare リポジトリ）
POST https://archive.softwareheritage.org/api/1/vault/git-bare/<swhid>/
# 3) status が done になるまでポーリング（new/pending → done/failed）
GET  https://archive.softwareheritage.org/api/1/vault/git-bare/<swhid>/
# 4) 完成物を取得して clone
GET  https://archive.softwareheritage.org/api/1/vault/git-bare/<swhid>/raw/
tar xzf <swhid>.tar.gz && git clone --no-checkout <dir>/*.git repos/<owner>__<name>
# 5) 02 と同じ sparse-checkout (*.java) + checkout HEAD
```

- 匿名 API は **120 req/時**。`scripts/09_swh.py` の `api()` が
  `X-Ratelimit-Remaining` と 429 を見て待機する。
- バッチは `scripts/10_swh_batch.py`。調理に数時間かかる巨大リポジトリがあるため、
  `ROUNDS`（ポーリング回数）と `GAP`（秒）で区切って実行する。

```bash
ROUNDS=8 GAP=900 nohup python3 scripts/10_swh_batch.py IonBazan/openapi-generator bshp/midpoint ... \
  > work/swh7.log 2>&1 &
grep -E 'ready|ok |failed|done,' work/swh7.log      # 進捗はラウンド行で判断
```

- 状態は `work/swh_state.jsonl` に追記（最新行が有効）。最終内訳は
  **復元 62 / 未収録 25 / 調理失敗 11 / 不正名 1**。
- **調理済みの bare リポジトリは保存していない**（`work/swh/` は空）。
  そのため `kind=swh` の `origin` は `work/swh/...` を指す死んだ URL になるが、
  blame は clone 内で完結するので実害はない。再取得したいときは `swhid` 列の ID で
  手順 2〜4 をやり直す（Vault の成果物は一定期間キャッシュされている）。
- 1 件だけ戻すなら `python3 scripts/10_swh_batch.py <owner>/<name>`。

## 3. 存命ミラー・改名先・上流プロジェクト（19 件）

SWH にすら無い `not_archived` のリポジトリは、コードそのものが別の場所に生きている。
**元のディレクトリ名のまま**、生きているリポジトリを clone して差し替える。

```bash
gh api repos/<owner>/<name>                       # まず 404（削除済み）を確認
gh api -X GET search/code -f q='"特徴的な行" language:Java'   # 改名先・フォーク・上流を探す
git clone https://github.com/<live-owner>/<live-name>.git repos/<owner>__<name>
python3 scripts/03_locate.py <owner>/<name>       # HEAD で特定
python3 scripts/08_history.py <function_id> ...   # HEAD に無ければ履歴から（ok_history）
```

| 元リポジトリ（`repo`） | 取得元 | 復元関数 |
|---|---|---:|
| LynzhX/android_frameworks_base-1 | `aosp-mirror/platform_frameworks_base`（全履歴 9.8GB） | 2 / 2 |
| sigurasg/ghidra | `NationalSecurityAgency/ghidra`（776MB） | 4 / 4 |
| sirivus/studioonline | `homacs/org.cakelab.blender.dna` | 2 / 2 |
| gerdreiss/camunda-dmn-xlsx | `camunda-community-hub/camunda-dmn-xlsx`（組織移転） | 1 / 1 |
| bgklika/aws-iot-device-sdk-java-v2 | `aws/aws-iot-device-sdk-java-v2` | 1 / 1 |
| slachiewicz/aws-sdk-java-v2 | `aws/aws-sdk-java-v2`（1.1GB） | 1 / 1 |
| kadenscott/DragonFight | `NerdNu/DragonFight`（組織移転） | 1 / 1 |
| zreed/webpieces | `deanhiller/webpieces`（改名） | 1 / 1 |
| mbreemhaar/webcurator | `WebCuratorTool/webcurator` | 1 / 1 |
| joaonmatos/clava | `specs-feup/clava` | 1 / 1 |
| nikosnikolaidis/stanbol | `apache/stanbol` | 1 / 1 |
| globalid/walletkit | `blockset-corp/walletkit` | 1 / 1 |
| coheigea/tcommon-studio-se | `gaozone-talend/tcommon-studio-se`（729MB） | 1 / 1 |
| mkpaz/telek | `mkpaz/corf`（改名） | 1 / 1 |
| derekdinan/CTFServer | `grahamedgecombe/opencraft` | 1 / 1 |
| good-loop/open-code | `sodash/open-code`（旧 `Licardo/AndroidChart` は別物） | 1 / 1 |
| javacreed/super-simple-stocks | `jainebri/Super-Simple-Stocks`（API が別物） | 0 / 1 |
| krisiney/SOKOBANFX-DMS-2020 | `hallvard/javafx` | 0 / 1 |
| nikosnikolaidis/commons-rdf | `apache/commons-rdf`（6.3MB） | 1 / 1 |

`good-loop/open-code` は最初に選んだ `Licardo/AndroidChart` が同名の別プロジェクトで、
`sodash/open-code`（Winterwell 系、`winterwell.maths` を含む）に差し替えて復元した。
`Clay-Ferguson/Quantizr` は履歴が 1 コミットに潰れていたため、上流の
`xmonader/quantizr` に差し替えて復元した（`src/main/java/quanta/actpub/ActPubService.java`）。
残る `javacreed/super-simple-stocks` と `krisiney/SOKOBANFX-DMS-2020` は、
公開されている fork が別の API に書き換わっていて関数本文が一致しない
（`weak_match` のまま）。記録としてディレクトリだけ残している。削除してよい。
`mkpaz/telek` と `derekdinan/CTFServer` は行単位の一致率では落ちていたが、
トークン単位の照合で同一メソッドと確認して復元した。

## 4. symlink による使い回し（4 件）

巨大な履歴を二重に持たないためのエイリアス。`03_locate.py` はディレクトリ名で
リポジトリを引くので、symlink でも通常のクローンと区別なく動く。

```bash
ln -s LynzhX__android_frameworks_base-1 repos/zipated__src
ln -s LynzhX__android_frameworks_base-1 repos/octo-online__reactive-aud
ln -s ../repos/lembergerth__cpachecker   repos/jcdubois__cpachecker
ln -s ../repos/metas-fresh__fresh        repos/focadiz__metasfresh
```

`zipated/src`（10 関数）は AOSP `frameworks/base` の履歴から、
`octo-online/reactive-aud` は該当コードが AOSP 本流に無く復元できなかった。

---

## 再現できたかの確認

```bash
python3 scripts/13_manifest.py                                   # マニフェスト再生成
git -C repos/<repo> rev-parse HEAD                               # head 列と一致するか
python3 scripts/03_locate.py <owner/name>                        # 同じ status/commit になるか
python3 scripts/05_verify.py --full                              # コメント 4750 件がすべて ok
```

## 注意

- `repos/` は `.gitignore` 対象（138GB）。**このリポジトリには入っていない**ので、
  別マシンでは上記 1〜4 を実行して作り直す。
- `git log -S` を使う `08_history.py` は全 blob を必要とする。SWH 由来の clone も
  02 と同じく全履歴を持つが、blob は `11_backfill_all.py` で補充済み。
- chromium のフォーク 2 件（`kjthegod/chromium`, `zealoussnow/chromium`）は
  全 clone が 100GB 超で取得していない。SWH の 2016 年スナップショットは Vault に無い
  （`POST /vault/git-bare/` が 404）。未復元 6 関数の主因。

## repo_missing の差し替え（上流・ミラー）

削除済みリポジトリの関数を、コード検索で見つけた上流・ミラーに差し替えて復元した
（2026-10-29）。ディレクトリ名は Tesoro 側の `repo` のまま。

| Tesoro の repo | 差し替え先 | 回収 |
| --- | --- | --- |
| zealoussnow/chromium | `kiwibrowser/src.next` | 9 |
| kimjand/cxf | `apache/cxf` | 5 |
| zem13579/hive | `apache/hive` | 4 |
| jd3538/Marist-Sakai-11.x | `sakaiproject/sakai` | 5 |
| fieldenms/（名前欠落） | `fieldenms/tg` | 3 |
| janjaali/azure-sdk-for-java | `Azure/azure-sdk-for-java` | 2 |
| IonBazan/openapi-generator | `OpenAPITools/openapi-generator` | 1 |
| bshp/midpoint | `Evolveum/midpoint` | 1 |
| kjthegod/chromium / sarang-apps/darshan_browser | `zealoussnow__chromium` への symlink | 1 |

残る `repo_missing` は `bcgov/jag-efax` / `fernandogomesfg/Algoritmos-em-Java` /
`ming1991/mingop` / `JC-Bodoque/geometric-transformer` の 4 件で、いずれも
GitHub 上に現存せず、コード検索でも同一本文が見つからない。

### bcgov/jag-efax の復元（完了）

Software Heritage には snapshot `e33d71a7`（最終訪問 2025-02-09）があるが、
`vault/gitfast` の調理要求（cook id `422257283`）が `status: new` のまま
進まない（flat 版 `422257535` も同じ）。API もレート制限に達したため中断。

判明していること:

- snapshot の HEAD は `51514339`（2024-10-30）。ツリー上の Java 89 ファイルに
  `hasTimedOut` / `SentMessage` は無く、データセットの版より新しい。
- したがって執筆コミットの特定には履歴（gitfast バルク）が必要。

再試行:

```bash
curl -s -X POST https://archive.softwareheritage.org/api/1/vault/gitfast/swh:1:rev:51514339d68c12a2ae81fea5c5bb3c638bb1b8f2/
curl -s https://archive.softwareheritage.org/api/1/vault/gitfast/swh:1:rev:51514339d68c12a2ae81fea5c5bb3c638bb1b8f2/
# status が done になったら fetch_url から tar を取得して repos/bcgov__jag-efax に展開
```

### kjthegod/chromium の差し替え（2026-10-29）

`kiwibrowser/src.next` は Chromium 本体ではなく差分のみで、`MediaDrmBridge.java` を
含んでいなかった（`not_at_head` の原因）。完全なミラー `ChromiumWebApps/chromium`
（2014 年の SVN スナップショット、2.1GB）に差し替えたところ、function 1656 が
比率 1.0 で一致し、コメントも exact で blame できた。

追記: gitfast バルクの調理が完了（502 リビジョン、数分）し、fast-import ストリームを
`work/jag.git` に取り込んで `repos/bcgov__jag-efax` に clone した。function 30563 は
比率 1.0 で一致し、コメント 2 件を blame できた（`4c63903378` / `898db744d6`）。

### 取得方法の棚卸し（2026-10-29）

`out/repo_manifest.csv` を実際のディレクトリと突き合わせて補完した。

| 種別 | 件数 | 意味 |
| --- | --- | --- |
| github | 874 | 存命リポジトリを GitHub から clone |
| swh | 62 | Software Heritage のバルクから復元（remote は `work/swh/...`） |
| mirror | 32 | 削除済みの代替（上流・fork・SWH バンドル） |
| symlink | 6 | 同一内容の clone を使い回し |

- 972 ディレクトリすべてが manifest に載り、HEAD のドリフトは 0。
- `bcgov__jag-efax` は SWH の gitfast バンドル（`work/jag.git`）を remote にしている。
- `fieldenms__unknown` は `fieldenms__` への、`sarang-apps__darshan_browser` は
  `zealoussnow__chromium` への symlink。
