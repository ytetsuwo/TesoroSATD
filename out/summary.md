# Tesoro provenance (repo / file / commit)

生成: 2026-11-03 21:06

## カバレッジ

- 関数: 1255 件中 1242 件を特定 (99.0%)、うち commit 取得 1236 件 (履歴から復元 59 件)
- コメント: 4981 件中 4961 件に commit を付与 (99.6%)、968 プロジェクト
- 関数一致度: {'high': 1126, 'medium': 105, 'low': 11}
- コメント特定方法: {'exact': 3492, 'structural': 1387, 'fuzzy': 8, 'relocated': 24, 'history': 21, 'reshaped': 6, 'position': 23} (exact=本文一致, structural=行末コメント・部分一致, fuzzy=本文が改変, position=記号のみ, relocated=別メソッドへ移動, history=履歴から復元, reshaped=形が変化)
- commit 日付範囲: 2000-12-06 〜 2026-08-31

## 状態

- ok: 1183
- ok_history: 59
- weak_match: 8
- unavailable: 5

## ラベル分布 (全件 / commit あり)

- DESIGN: 697 / 693
- IMPLEMENTATION: 474 / 470
- NONSATD: 3432 / 3423
- DEFECT: 220 / 217
- TEST: 78 / 78
- DOCUMENTATION: 80 / 80

## ファイル

| ファイル | 行 | 内容 |
| --- | --- | --- |
| `out/tesoro_provenance.csv` | 4981 | コメント単位の来歴 (下記の列) |
| `out/tesoro_msr16.csv` | 4981 | Maldonado MSR16 形式 (3 列) |
| `out/tesoro_msr16_versioned.csv` | 4981 | 同上、projectname に commit を付加 |

`tesoro_provenance.csv` の列: function_id, comment_id, projectname,
classification, commenttext, repo, path, path_at_commit, start_line,
comment_line, n_comment_lines, commit_id, commit_short, commit_url,
blame_url, commit_author, commit_date, match, function_ratio, status, at_head,
snapshot_commit, path_snapshot

## 既知の制約

- `unavailable`: リポジトリが GitHub 上で削除され、Software Heritage にも
  使えるスナップショットが無いもの (来歴は復元できない)。
- `ok_history`: HEAD には無いが、履歴 `git log -S` で見つけたもの。
  `path` と行番号はその commit 時点のもの (`at_head=false`)。
- `not_at_head` / `weak_match`: 履歴を探しても見つからなかったもの。
- `commit_id` はコメント行の blame なので、関数本体の最終変更とは異なる。
- `projectname` は `owner/name`。Maldonado の `project-version` に相当する
  版情報は commit で表す (`tesoro_msr16_versioned.csv`)。
- `NONSATD` は msr16 形式では `WITHOUT_CLASSIFICATION` に改名。

## 再現方法

```
python3 scripts/01_repo_meta.py    # GitHub API で生死と既定ブランチ
python3 scripts/02_clone_repos.py  # blob なし部分クローン + *.java checkout
python3 scripts/03_locate.py       # 関数アラインメント + git blame
python3 scripts/04_export.py       # CSV / レポート（27/28 の結果を反映）
python3 scripts/05_verify.py --full  # 全行のテキスト検証
python3 scripts/06_blame.py 8431   # 個別の来歴と blame
```

## 来歴の厳密監査

来歴付き件数と検証の確実性は別集計です。全コメントの個別版を検査しています。

- 確定: 4621
- 要確認: 337
- 不一致: 3
- 検証不能: 0
- 未対応: 20

確定は本文・関数所属・本文行の最終更新者についての自動判定であり、
最初の導入コミットや同型コピーの唯一の起源を保証しません。
詳細: `out/strict_audit_summary.md` / `out/strict_audit.jsonl`。

## SATD 導入コミット

現在のコメント本文が最初に現れたコミットを、履歴の親方向へ確認した別集計です。

- 導入コミットを特定: 4922
- 記録済み `commit_id` と同一: 4526
- 記録済み `commit_id` と異なる: 396
- 曖昧: 0
- 未解決: 0 + 打ち切り 0
- 本文未確定のため対象外: 39

データセット本文の履歴特定: 4958 （完全一致 4930、履歴で完全一致 8、候補 2、近い候補 1、接頭辞 1、周辺コードから推定 24）。
短語のため対象外: 2、本文が履歴に見つからない: 1。

本文が後から書き換えられている場合、`introduced_in` は現在の本文を導入した
コミットであり、SATD コメント全体の最初の導入とは限りません。
詳細: `out/introduced_summary.md` / `out/tesoro_introduced.csv`。
