# TesoroSATD — Tesoro データセットの来歴 (project / file / commit) 付与

[Tesoro](https://github.com/NamCyan/tesoro) (TOSEM 2026) の SATD データは
「コメント本文 + 関数本文」だけで、どのリポジトリのどのファイル・どのコミットかが
分からない。このリポジトリは、各コメントを GitHub 上の実ファイル・実コミットに
結び付け、`git blame` できる形にするパイプラインと成果物をまとめたもの。

## 成果物

| ファイル | 行数 | 内容 |
| --- | --- | --- |
| `out/tesoro_provenance.csv` | 4981 | コメント単位の来歴 (repo / path / 行 / commit / 作者 / 日付) |
| `out/tesoro_msr16.csv` | 4981 | Maldonado MSR16 形式 `projectname,classification,commenttext` |
| `out/tesoro_msr16_versioned.csv` | 4981 | 同上、`projectname` を `owner/name@<sha7>` にした版 |
| `out/functions_located.jsonl` | 1255 | 関数単位の生データ (アラインメント結果と全コメント) |
| `out/summary.md` | – | カバレッジと制約の自動レポート |
| `out/repo_manifest.csv` | 960 | 各 clone の入手経路 (`kind` / 取得元 URL / SWH swhid / HEAD) |

カバレッジ: **関数 1229/1255 (97.9%)、コメント 4924/4981 (98.9%)** に commit を付与。
うち 50 関数は HEAD ではなく履歴から復元 (削除リポジトリは Software Heritage・
存命ミラー・上流プロジェクトから復元)。950 プロジェクト、commit 日付は
2000-12-06 〜 2026-08-31。

各 clone をどこからどう取得したかは **[docs/repo_acquisition.md](docs/repo_acquisition.md)**
に手順つきで記録している (`out/repo_manifest.csv` に 960 件の一覧)。

## 使い方

```bash
# あるコメントの来歴を見る (引数は function_id か comment_id)
python3 scripts/06_blame.py 8431

# 直接 git blame する
git -C repos/pagarme__pagarme-core-api-java \
    blame -L 39,41 -- src/main/java/me/pagar/api/models/CancelSplitRequest.java
# -> deaf49ffa1 Jeferson 2021-12-28  /** GETTER
```

CSV の `commit_id` は**コメント行の blame 結果**なので、そのコメントが最後に
書かれたコミット。関数本体の最終変更とは一致しないことがある。
`blame_url` を開けば GitHub 上でも同じ行を blame できる (`path_at_commit` は
その commit 時点のパスなので、リネーム前でも URL が壊れない)。

`at_head` は「HEAD 上のファイルで確認できたか」、`snapshot_commit` は履歴から
復元した場合の基準コミット。`at_head=false` の行は `path` と行番号がその
commit 時点のもの。

## パイプライン

Python 3.9+ と `tree-sitter` / `tree-sitter-java` が必要。

```bash
python3 -m pip install -r requirements.txt   # tree-sitter, tree-sitter-java
python3 scripts/01_repo_meta.py     # GitHub API: 974 リポジトリの生死・既定ブランチ
python3 scripts/02_clone_repos.py   # 存命 875 件を全履歴で clone (blob なし + *.java checkout)
python3 scripts/03_locate.py        # 関数をアラインメント (AST で範囲確定) し、コメント行を blame
python3 scripts/04_export.py        # CSV とレポート
python3 scripts/05_verify.py --full # 全行のテキスト検証
python3 scripts/08_history.py       # HEAD に無い関数を履歴から探索
python3 scripts/09_swh.py           # 削除リポジトリを Software Heritage から復元
python3 scripts/10_swh_batch.py     # 大型リポジトリの一括調理・ポーリング
python3 scripts/11_backfill_all.py  # 全クローンの blob を取得 (オフライン化)
python3 scripts/12_crossrepo.py     # 削除リポジトリの関数を他の clone から探索
python3 scripts/13_manifest.py      # 各 clone の入手経路を out/repo_manifest.csv に
python3 scripts/14_check_span.py    # 全コメントが関数内/直上 javadoc か検算 (AST)
python3 scripts/15_ast_analysis.py  # AST とデータセット関数の突き合わせ (work/ast_analysis.jsonl)
python3 scripts/16_symbol_history.py # 記号だけのコメントを履歴から復元
python3 scripts/17_history_text.py   # HEAD から消えた本文を履歴から復元
python3 scripts/18_reshape.py        # 本文はあるが形が変わったブロックを配置
python3 scripts/19_deep_history.py   # 改名・改変を許した履歴探索
```

### 1. クロール (01, 02)

`tesoro_code.json` の `repo` (`owner/name`) を GitHub API で照会し、
**875 件存命 / 99 件削除・非公開**を判定。存命分は

```bash
git clone --filter=blob:none --no-checkout --single-branch <url>
git sparse-checkout set --no-cone '*.java' && git checkout HEAD
```

で**全履歴を持つが作業ツリーは Java だけ**のクローンを作る。
その後 `11_backfill_all.py` で全 blob を取得済みなので (`repos/` 960 件, 138GB)、
`git blame` / `git log -S` はネットワークなしで動く。

> 注: 匿名の部分クローンは数百リクエストで HTTP 400 になる。`git_env()` が
> `url.https://x-access-token:<token>@github.com/.insteadOf` を環境変数で注入して回避。

### 2. 特定 (03)

1. 関数の代表テキストを決め、長い行をアンカーに `rg -F` で候補ファイル・行を列挙。
2. 最長アンカーを起点に候補オフセットを作り、`difflib` で 2 通りに採点する。
   **行単位**の `SequenceMatcher` と、コメントを除いた**トークン列**の最密一致区間
   (行数変化に強い)。`max(行, トークン)` を採り、トークン一致のときは宣言名が窓の先頭付近に
   あること、同じ本文が複数ファイルに無いことを確認したうえで
   `high ≥0.9 / medium ≥0.7 / low ≥0.45` に分類。
3. 関数の範囲は Java の AST (tree-sitter) から取る。アラインメントの `start` が
   メソッド本体の途中に落ちた場合は、それを囲む宣言まで広げる (`ast_span_shift`)。
   宣言名が食い違い一致率も低い候補は捨てて履歴探索に回す。
   コメントは**直上の javadoc 〜 関数本体**から
   **exact** (本文一致) → **structural** (行末コメント・部分一致) →
   **fuzzy** (本文が改変。単語列の一致率 ≥0.80) → **position** (本文が記号だけの
   コメントを、前後の対応付けに挟まれた範囲で記号一致により配置) →
   **relocated** (リファクタで別メソッドへ移った本文一致のコメント) の順に照合し、
   **コメントノードの行以外は受け付けない**。ブロック内の空行は内容行として
   数えない (隣接コメントが空行で区切られていても 1 つのコメントとして照合する)。
4. 該当行を `git blame --line-porcelain` して commit / 作者 / 日時 / その
   コミット時点のパスを取得し、`git log --follow` でリネーム前のパスも解決。

### 3. 履歴探索 (08)

HEAD に同一関数が無い場合、`git log --all -S<anchor> --name-only -- '*.java'` で追加・削除
されたコミットとパスを集め、各候補を `git show <sha>:<path>` でアラインメントし
直して最良の commit を採用する (`ok_history`)。

### 4. 削除リポジトリの復元 (09, 10)

GitHub から消えた 99 件は Software Heritage の Vault で復元する。

1. `GET /origin/<url>/visits/` で最新の `git` ビジットを選び `swh:1:snp:...` を得る。
2. `POST /vault/git-bare/<swhid>/` で調理を依頼し、`done` になるまでポーリング。
3. `fetch_url` から bare リポジトリの tar.gz を取得して clone、`*.java` を checkout。

匿名 API は **120 リクエスト/時**なので、`10_swh_batch.py` は全件を一括で調理依頼
してから 15 分おきにまとめて確認する (1 件ずつ待つと巨大リポジトリで詰まる)。
最終状態: 復元 62 / 未収録 25 / 調理失敗 11 / 不正名 1。
Vault の調理に失敗した 3 件 (`jcdubois/cpachecker`, `focadiz/metasfresh`,
`coheigea/tcommon-studio-se`) は、同一プロジェクトの存命ミラーから clone して補った
(commit SHA は共通なので blame はそのまま使える)。`chromium` の fork 2 件は
巨大すぎて Vault が調理できず、部分取得では来歴を保証できないため未復元のまま。
なお `repos/jcdubois__cpachecker` と `repos/focadiz__metasfresh` は既存ミラーへの
symlink で、`repos/coheigea__tcommon-studio-se` は
`gaozone-talend/tcommon-studio-se` の clone。

SWH に未収録 (`not_archived`) だった 25 件は、GitHub のコード検索で特徴行を探し、
改名・組織移転先や上流プロジェクトを見つけて復元した (25 件中 13 件・27 関数)。

- `zipated/src` (10) と `LynzhX/android_frameworks_base-1` (2) は AOSP
  `platform_frameworks_base` の全履歴から `WifiStateMachine.java` 等を特定。
- `sigurasg/ghidra` (4) は `NationalSecurityAgency/ghidra` の履歴から
  `CppCompositeTypeTest.java` を特定。
- 改名・移転: `mkpaz/corf` ← telek (未復元)、`NerdNu/DragonFight`、
  `camunda-community-hub/camunda-dmn-xlsx`、`deanhiller/webpieces`、
  `WebCuratorTool/webcurator`、`specs-feup/clava`、`blockset-corp/walletkit`。
- 上流プロジェクト: `aws/aws-sdk-java-v2`、`aws/aws-iot-device-sdk-java-v2`、
  `apache/stanbol`、`homacs/org.cakelab.blender.dna`。

### 5. 検証 (05)

- `--full`: commit が付いた全 4924 コメントで、ファイル上のコメント本文が一致する
  ことを確認 → **4924/4924**。
- ランダム 120 件で「本文一致・blame 一致・その commit に本文が存在」の 3 点を
  確認 → **120/120**。
- `14_check_span.py` で全コメントが**関数本体（宣言行〜閉じ括弧）または直上の
  javadoc**に入っているかを AST で検算 → 本体 4306 / 宣言部 16 / 直上 556 /
  履歴から復元 27 / 別メソッドへ移動 19 / 本体外 0。
  マップされた行が実際にコメントノードかも同時に確認している。

## 既知の制約

- **repo_missing 23 関数 / 15 リポジトリ**: GitHub 上で削除済みで、SWH と上流の
  どちらからも復元できなかったもの。内訳は SWH に未収録かつ上流も辿れない 6 リポジトリ
  (8 関数)、SWH の調理失敗 8 リポジトリ (14 関数, chromium の fork 2 件 6 関数、
  hive 3、cxf / midpoint / azure-sdk-for-java / openapi-generator / Marist-Sakai 各 1)、
  不正なリポジトリ名 1 件 (1 関数)。
- **weak_match 4 関数** (いずれも「その版のコードが公開されていない」):
  `leusonmario/2022PhDThesis` の randoop 生成テスト (同じ `test20046` が 83 ファイルに
  重複し来歴を一意に決められない。`work/manual_reject.jsonl` に理由つきで登録)、
  `javacreed/super-simple-stocks` (元リポジトリは削除済み。現存する fork は
  `calculatePERatio` に書き換わっていて `computePeRatio` が無い)、
  `octo-online/reactive-aud` (削除済み。関数は AOSP の
  `tools/lock_agent/.../LockHook.java` 由来で、手元の AOSP ミラーは古く該当しない)、
  `krisiney/SOKOBANFX-DMS-2020` (削除済み。公開されている fork は
  `keyPressed(KeyCode)` 版で、`handleKey(KeyCode)` 版が無い)。
  行単位の一致率だけでは `mkpaz/telek` と `derekdinan/CTFServer` も落ちていたが、
  トークン照合で同一メソッドと確認できたため復元済み。
  `nikosnikolaidis/commons-rdf` は上流の選定を誤っており (rdf4j ではなく
  `apache/commons-rdf` が正しい)、差し替えて復元した。
- 照合したメソッド名がデータセット側の宣言名と違い、一致率も 0.7 未満の場合は
  `not_at_head` として履歴探索 (08) に回す。リファクタで別クラスへ移った関数
  (`Workinq/AsyncCollectors` の `populateGui` → `ContentsGui`) や、同型のテストが
  並ぶ `CBIIT/camod` の 8 関数は、これで正しいメソッドに復元できた。
- `alnum()` / `tokens()` は NFKC 正規化したうえで `\w` を残すので、中国語・
  キリル文字だけのコメントも照合できる (fuzzy はトークンが少ないとき文字列比較に
  切り替える)。これで `9444` / `11434` / `18082` / `24804` の非 ASCII コメントを
  回収した。
- `projectname` は `owner/name`。Maldonado の `project-version` に当たる版情報は
  commit で表し、必要なら `tesoro_msr16_versioned.csv` を使う。
- Maldonado 形式に合わせて `NONSATD` → `WITHOUT_CLASSIFICATION` に改名している
  (provenance CSV は元のラベルを保持)。

## ディレクトリ

```
tesoro/     Tesoro 本家のデータ (sparse clone, data/ 184MB)
repos/      clone 960 件 (875 存命 + SWH・ミラー・上流復元 + symlink 4 件, owner__name, 全履歴 + *.java)
scripts/    パイプライン (01〜19)
work/       中間状態・ログ (repo_meta.json, clone_state.jsonl, swh_state.jsonl, locate.log)
out/        成果物 (CSV / JSONL / summary.md / repo_manifest.csv)
docs/       入手経路の記録 (repo_acquisition.md)
```
