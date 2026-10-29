# AGENTS.md

## このリポジトリ

Tesoro データセット（Java の SATD コメント 4,981 件 / 関数 1,255 件）について、
**各コメントがどのリポジトリ・ファイル・コミットで書かれたか**を特定するパイプライン。
成果物は `out/` の CSV で、`repos/`（100GB 超）は Git 管理外。

## セットアップ

```bash
python3 -m pip install -r requirements.txt   # tree-sitter==0.23.2, tree-sitter-java==0.23.5
python3 scripts/00_pin_repos.py --check      # clone が manifest の HEAD か確認（ドリフト 0 が正常）
```

`repos/` が無い場合、`scripts/01`〜`12` で再取得する（`docs/repo_acquisition.md` 参照）。

## よく使うコマンド

```bash
./reproduce.sh                     # 00→03→08→16→17→18→21→25→04→05→24→23 の通し実行
python3 scripts/03_locate.py --all # 関数の再照合（約 5 分）
python3 scripts/04_export.py       # CSV / summary.md の再生成
python3 scripts/05_verify.py --full# 全行の本文検証（0 bad が正常）
python3 scripts/05_verify.py 120   # ランダム 120 件で text/blame/show
python3 scripts/24_consistency.py  # データ・CSV・README・manifest の整合性
python3 scripts/06_blame.py 8431   # 個別の来歴確認
```

## 構成

| パス | 内容 |
|---|---|
| `scripts/00_pin_repos.py` | clone を manifest の HEAD に固定/検査 |
| `scripts/01`〜`12` | 取得・履歴探索・SWH 復元・横断探索 |
| `scripts/03_locate.py` | 関数の照合とコメント対応付け（中核） |
| `scripts/04_export.py` | CSV と `out/summary.md` |
| `scripts/05_verify.py` | 検証（text / blame / show） |
| `scripts/08_history.py` | 履歴探索（`--remap` でコメント再マップ） |
| `scripts/14`〜`18` | AST 検算・記号コメント・消えた本文・形の変化 |
| `scripts/21_crossfile.py` | 同一リポジトリ内の別コピー探索 |
| `scripts/22_name_search.py` | メソッド名で別クローン探索 |
| `scripts/25_manual_fixes.py` | 手動対応付けの明示（再現用） |
| `scripts/astjava.py` | tree-sitter のラッパ |
| `out/` | 成果物（CSV / JSONL / summary / environment） |
| `work/` | 中間状態・ログ・バックアップ |
| `docs/repo_acquisition.md` | リポジトリ入手経路と復元手順 |

## データの不変条件

- `out/tesoro_provenance.csv` は **4,981 行**（Tesoro のコメント数と一致）。
- `out/functions_located.jsonl` は **1,255 関数**。
- `status`（関数）と `commit_id`（コメント）は別の粒度。`status=ok` でもコメントが未対応のことがある。
- `at_head=false` の行は `snapshot_commit` と `path_snapshot` を使う。
- `projectname` は `owner/name`。MSR16 の版情報は `@sha7`（`tesoro_msr16_versioned.csv`）。

## 変更時のルール

1. **スクリプト番号は振り直さない**。新しい段は末尾に追加する。
2. 照合ロジックを変えたら `03 --all` → 後段（08/16/17/18/21/25）→ `04` → `05` まで通す。
3. 数値が変わったら **README と `out/summary.md` の両方**を更新する
   （`24_consistency.py` が不一致を検出する）。
4. 手で対応付けたものは必ず `scripts/25_manual_fixes.py` に追記する（再現性のため）。
5. 誤対応は入れない。閾値未満は未対応のまま残す（`05 --full` が 0 bad であること）。
6. コミットメッセージは日本語で、件数の変化を含める（例: `… を復元 (コメント 4,958/4,981)`）。

## 落とし穴

- **clone の HEAD が動くと結果が変わる**。再現前は `00_pin_repos.py --pin`。
- `git log -S` は blob:none の部分クローンだと極端に遅い（Chromium では実用外）。
- `repos/` の symlink（6 件）は意図的。消さない。
- `work/manual_reject.jsonl` は自動判定から除外する ID（randoop の重複など）。
- SWH の vault 調理は `status: new` のまま止まることがある。完了後に
  `git fast-import` で取り込む（`docs/repo_acquisition.md`）。
- 巨大リポジトリ（Chromium 67GB）は `--filter=blob:none --sparse` か `--no-checkout` を使う。

## 完了条件

```bash
python3 scripts/05_verify.py --full   # text: N ok / 0 bad
python3 scripts/05_verify.py 120      # text/blame/show 120/120
python3 scripts/24_consistency.py     # OK
python3 scripts/00_pin_repos.py --check  # drifted: 0
```

## 現在の状況（2026-10-30 時点）

- コメント **4,958 / 4,981 (99.5%)**、967 プロジェクト
- `weak_match` 16 コメント / 8 関数、`ok` 本文不明 2、`unavailable` 5
- `reproduce.sh` は手動対応付け 12 件を含めて再現可能（一部 Chromium 系は未統合）
