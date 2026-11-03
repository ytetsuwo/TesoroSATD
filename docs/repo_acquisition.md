# リポジトリの入手経路（再現手順）

`repos/` の 975 ディレクトリがどこから来たのかを、後から同じ状態に戻せる形で記録する。
機械可読な一覧は **`out/repo_manifest.csv`**（1 行 = 1 ディレクトリ）。

```bash
python3 scripts/13_manifest.py          # 975 行を再生成
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

現状の内訳: **github 874 / swh 64 / mirror 32 / symlink 5**。

---

## 1. GitHub からの直接クローン（874 件）

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

## 2. Software Heritage からの復元（Vault 62 件 + バンドル 1 件 + browse page 1 件）

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

## 3. 存命ミラー・改名先・上流プロジェクト（20 件）

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
| javacreed/super-simple-stocks | `albertattard/super-simple-stocks`（SWH browse page から履歴復元） | 1 / 1 |
| krisiney/SOKOBANFX-DMS-2020 | `hallvard/javafx` | 0 / 1 |
| nikosnikolaidis/commons-rdf | `apache/commons-rdf`（6.3MB） | 1 / 1 |
| octo-online/reactive-aud | `octo-online/reactive-audit`（改名） | 3 / 3 |

`good-loop/open-code` は最初に選んだ `Licardo/AndroidChart` が同名の別プロジェクトで、
`sodash/open-code`（Winterwell 系、`winterwell.maths` を含む）に差し替えて復元した。
`Clay-Ferguson/Quantizr` は履歴が 1 コミットに潰れていたため、上流の
`xmonader/quantizr` に差し替えて復元した（`src/main/java/quanta/actpub/ActPubService.java`）。
`javacreed/super-simple-stocks` は `albertattard/super-simple-stocks` の
`Stock.java` 115〜121 行を SWH の browse page から復元し、コメントを
commit `0136cb593d`（2017-11-03, Albert Attard）に紐付けた。
この 1 件は `swhid` を持たないため、次のコマンドで再構築する。

```bash
python3 scripts/29_swh_browse_restore.py repos/javacreed__super-simple-stocks \
  --head 5cda4140df3b6fa688c2891606fe5c88de4d5afa --keep 7
```

`--keep` は HEAD から遡るコミット数。`--log` に revision log の JSON を渡すと
SWH API の 1 回の呼び出しを省略できる。スクリプトは各 tree / commit の SHA を
SWH の ID と突き合わせ、末尾を `.git/shallow` で切る。
`krisiney/SOKOBANFX-DMS-2020` は関数自体は `ok` だが、2 コメントのうち
Javadoc 1 件が未対応のまま残っている。
`mkpaz/telek` と `derekdinan/CTFServer` は行単位の一致率では落ちていたが、
トークン単位の照合で同一メソッドと確認して復元した。

## 4. symlink による使い回し（5 件）

巨大な履歴を二重に持たないためのエイリアス。`03_locate.py` はディレクトリ名で
リポジトリを引くので、symlink でも通常のクローンと区別なく動く。

```bash
ln -s LynzhX__android_frameworks_base-1 repos/zipated__src
ln -s ../repos/lembergerth__cpachecker   repos/jcdubois__cpachecker
ln -s ../repos/metas-fresh__fresh        repos/focadiz__metasfresh
```

`zipated/src`（10 関数）は AOSP `frameworks/base` の履歴から復元した。
`octo-online/reactive-aud` は AOSP の symlink では一致せず、改名先の
`octo-online/reactive-audit` を clone して 3 コメントを復元した。

---

## 再現できたかの確認

```bash
python3 scripts/13_manifest.py                                   # マニフェスト再生成
git -C repos/<repo> rev-parse HEAD                               # head 列と一致するか
python3 scripts/03_locate.py <owner/name>                        # 同じ status/commit になるか
python3 scripts/05_verify.py --full                              # コメント 4750 件がすべて ok
```

## 注意

- `repos/` は `.gitignore` 対象（242GB）。**このリポジトリには入っていない**ので、
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
| ming1991/mingop | `cpoopc/RetrofitRxCache` | 2 |

残る `repo_missing` は `fernandogomesfg/Algoritmos-em-Java` /
`JC-Bodoque/geometric-transformer` の 2 件で、いずれも GitHub 上に現存せず、
コード検索でも同一本文が見つからない。2026-11-04 時点の探索記録:

| リポジトリ | 確認したこと |
| --- | --- |
| `fernandogomesfg/Algoritmos-em-Java` | GitHub API は 404。所有者の公開リポジトリに Java は無い。`FuncProdutividade` / `addFuncprodutividade` / `printSalarioProdutividade` は GitHub code search（`fork:true` を含む）で 0 件、Sourcegraph でも 0 件。SWH の origin search に登録なし。GitLab / Bitbucket にも無い。Internet Archive の CDX に snapshot なし。GH Archive では 2021-09-29〜2022-06-14 に 39 イベント（Push 38・Create 1、Fork 0）。`FuncProdutividade.java` の作成は `d48d1a2a871c2be2c1a5960a043825afcfefc805`（2022-05-01）だが、SWH に revision が無く、現存する同名 fork 106 件の履歴にも一致しない。 |
| `JC-Bodoque/geometric-transformer` | GitHub API は 404。`TODO: translate code` と `translate()` の組合せは GitHub code search / Sourcegraph で 0 件。SWH の origin は登録されているが、2023 年の 3 回の visit はすべて `not_found` で snapshot が無い。同名の公開リポジトリ 78 件も Java の一致なし。GH Archive では 2021-05-14〜2021-07-22 に 6 イベント。PR #2 の head `b1fd35f5c3c3ed2e20c9e89f3d85007248b3d48c`、merge `bb8031c940bf750da7abe0ae8a59e00e902273c2`、base `b61c9e7729bb7a5eee753dd69ca75c61922893d7` まで特定したが、Fork イベントは無く、SWH に revision が無い。 |

`30244:0` の `krisiney/SOKOBANFX-DMS-2020` は、GH Archive で
2021-12-22 の作成時に README / LICENSE だけが push され、現在の
`c6c7ca4`（2023-10-17）は親のない単一コミットであることを確認した。
データセットの Javadoc はその前の版のもので、現存する
`Yixin-Shen-1218/Sokoban`（同じ `c6c7ca4`）にも履歴が無い。
`TODO: implement something funny` の現存コピーは 1 件だけで、
Javadoc の完全一致は GitHub code search で 0 件だった。
2026-11-04 には GitHub の `Sokoban language:Java` 666 リポジトリ
（433 + 233）と GitLab の `ae2dms` 4 リポジトリ、`com.ae2dms` を含む
10 リポジトリの全履歴も走査したが、旧 Javadoc は見つからなかった。
作者の連絡先は `shenyixin1218@163.com`（`c6c7ca4` の author）。

`ming1991/mingop` は `cpoopc/RetrofitRxCache` に同一コードが現存し、
`AsyncOnSubscribeCacheNet.java` の 2 箇所（122 行 / 161 行）が
commit `ebb26e1c`（2016-05-15）で導入されたことを確認した。

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
| swh | 64 | Software Heritage のバルクから復元（remote は `work/swh/...`） |
| mirror | 32 | 削除済みの代替（上流・fork・SWH バンドル） |
| symlink | 5 | 同一内容の clone を使い回し |

- 975 ディレクトリすべてが manifest に載り、HEAD のドリフトは 0。
- `bcgov__jag-efax` は SWH の gitfast バンドル（`work/jag.git`）を remote にしている。
- `fieldenms__unknown` は `fieldenms__` への、`sarang-apps__darshan_browser` は
  `zealoussnow__chromium` への symlink。

### 再解析テスト（2026-10-29）

`03 --all` → `08 --remap` → `16` → `17` → `18` → `21` を再実行したところ、
**4,957 件（+1）だが内容は不一致**だった。

- 誤って増えたもの: 2495（0.23 の低類似）、3182（`Table.java` に無い本文）、
  10862 の重複 3 件。
- 失われたもの: 15634 c14、16350、20776、20778（手作業で入れた対応付け）。
- 原因: 手動修正がスクリプト化されておらず、`17`/`21` が低類似・重複を
  受け入れてしまう。

再解析後は `work/fl_before_rerun.jsonl` に戻し、検証済みの 4,956 件を維持した。
完全再現には `25_manual_fixes.py`（手動対応付けの明示）と、
`17`/`21` の受理条件の厳格化が必要。

### 完全再現の実装状況（2026-10-29）

`reproduce.sh`（00 → 03 → 08 → 16 → 17 → 18 → 21 → 25 → 04 → 05 → 24）を追加し、
手動対応付けは `scripts/25_manual_fixes.py` に明示した。17 と 21 は受理条件を
厳格化（類似度 0.85 / 完全一致・0.95）した。

スパンとリビジョンを修正した結果、`reproduce.sh` は **4,957 件 / 0 bad** を
再現する（手動対応付け 12 件を含む）。再実行で 3182 の `//noinspection` が
履歴から追加で見つかり、4,956 → 4,957 になった。

### Chromium 系の部分取得（2026-10-29）

`chromium/chromium` を `--filter=blob:none --sparse` で 4.9GB に抑えて取得し、
`media/base/android/java/src/org/chromium/media/` を checkout した。

- 16761 `// TODO(qinmin): May need to report the error to the caller. crbug.com/356498.`
  → `MediaCodecBridge.java:1007`、commit `e272282175ff95`（qinmin, 2014-03-26）
- 16764 `// TODO(dalecurtis): We may want to drop transient errors here.`
  → `MediaCodecBridge.java:384`、commit `e048085aee3fb3`（Dale Curtis, 2018-08-15）

スパース clone を `repos/zealoussnow__chromium` に置くと、`chrome/browser/...` を
必要とする 728 などが読めなくなるため、本体は Kiwi の clone のままにし、
上記 2 件は未統合（`repos/_chromium` に証跡を保持）。

### Chromium 3 件の特定（2026-10-30・フルクローン）

`chromium/chromium` を `--no-checkout` で 64GB 取得し、本文で `git log -S`:

| 関数 | コメント | 追加コミット | 位置（当時） |
| --- | --- | --- | --- |
| 19585 | `// Ignores the service worker payment apps in WebLayer until -` | `f019e0c2f94e0`（Liquan (Max) Gu, 2021-06-30） | `weblayer/.../payments/WebLayerPaymentRequestService.java` 63 行 |
| 20104 | `/** Adds a bookmark with the given {@link Tab} ... */` | `611d21bc6f867a`（Brandon Wylie, 2021-09-27） | `chrome/android/.../bookmarks/BookmarkUtils.java` 236 行 |
| 24102 | `/** Tests that pressing the app button ... */` | `d48d2b1639774`（bsheedy, 2019-03-13） | `chrome/android/javatests/.../vr/VrBrowserNativeUiTest.java` 703〜706 行 |

19585 と 20104 は、`zealoussnow__chromium` に `_chromium_full` から該当コミットを fetch し、
`25_manual_fixes.py` に `at_commit` 付きで登録して CSV に統合した。
24102 も当時の本文が一致したため統合。

### weak_match の追加解決（2026-11-03）

- `10862` (`leusonmario/2022PhDThesis`): `test20046` は 83 ファイルに重複するが、
  jsoup の本文と一致するのは
  `jsoup/a8b6982de98ff76ef254031d7152fff57f6bf941/randoop_6/RegressionTest40.java`
  1455〜1488 行だけ。3 コメントは commit `efafe731`（2022-02-17, leusonmario）で
  導入された。`25_manual_fixes.py` の `WHOLE_FIXES` に登録した。
- `19374` (`octo-online/reactive-aud`): リポジトリは
  `octo-online/reactive-audit` に改名されていた。`HistoryStackElement.java`
  34〜44 行の 3 コメントを、`c7c501d6`（2014-06-20, WARNING）と
  `64855eb4`（2014-07-07, 残り 2 件）に紐付けた。
- `13571` (`javacreed/super-simple-stocks`): 元リポジトリは削除済みだが、同一コードは
  `albertattard/super-simple-stocks` の `Stock.java` 115〜121 行に現存する。
  SWH の Vault が `new` のまま進まないため、browse page から
  `0136cb593d` までの 7 コミットを git オブジェクトとして再構築し、
  コメントを commit `0136cb593d`（2017-11-03, Albert Attard）に特定した。

### Chromium の weak_match 5 件（2026-11-04）

`_chromium_full`（64GB、全履歴）から旧版のファイルと行を特定し、
`zealoussnow__chromium` に `objects/info/alternates` を追加して同じ履歴を参照できるようにした。
`25_manual_fixes.py` の `HISTORY_FIXES` に以下を登録した。

| 関数 | スナップショット | ファイル | コメント行 | コミット |
| --- | --- | --- | --- | --- |
| 16761 | `e272282175ff` | `media/.../MediaCodecBridge.java` | 404 | `e272282175ff` (qinmin, 2014-03-26) |
| 16764 | `e048085aee3f` | `media/.../MediaCodecBridge.java` | 217 | `e048085aee3f` (Dale Curtis, 2018-08-15) |
| 19585 | `f019e0c2f94e` | `weblayer/.../WebLayerPaymentRequestService.java` | 63–64 | `f019e0c2f94e` (Liquan Gu, 2021-06-30) |
| 20104 | `42fa57e9aa3c` | `chrome/.../bookmarks/BookmarkUtils.java` | 250–258, 272–274, 282, 286, 295 | `611d21bc6f86` / `42fa57e9aa3c` / `3714099df5fd` / `611d21bc6f86` / `4699d649c383` |
| 24102 | `d48d2b163977` | `chrome/.../vr/VrBrowserNativeUiTest.java` | 703–706 | `d48d2b163977` (bsheedy, 2019-03-13) |

これで `weak_match` は 0 関数になった。

### `2495` の Javadoc 復元（2026-11-04）

`fieldenms/unknown` の `EntityCentre.java` は、2025-09-19 の commit `4a88bfb8` で
`/** ... */` が `///` 形式へ変換されていた。変換前の `4a88bfb8^` には
dataset と同一の Javadoc が 1439〜1445 行に残っており、すべて
`6711ae6d`（2015-04-29, Jhou）に blame できる。
`25_manual_fixes.py` の `FIXES` にこの履歴位置を追加し、コメント 0 を復元した。
残る未対応は 4 件（削除リポジトリ 3 件 + `30244` の Javadoc 1 件）。
