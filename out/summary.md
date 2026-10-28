# Tesoro provenance (repo / file / commit)

生成: 2026-10-28 16:47

## カバレッジ

- 関数: 1255 件中 1226 件を特定 (97.7%)、うち commit 取得 1219 件 (履歴から復元 50 件)
- コメント: 4981 件中 4753 件に commit を付与 (95.4%)、947 プロジェクト
- 関数一致度: {'high': 1011, 'medium': 198, 'low': 17}
- コメント特定方法: {'exact': 3378, 'structural': 1375} (exact=本文一致, structural=改変/行末コメント)
- commit 日付範囲: 2000-12-06 〜 2026-08-31

## 状態

- ok: 1176
- ok_history: 50
- repo_missing: 23
- weak_match: 6

## ラベル分布 (全件 / commit あり)

- DESIGN: 697 / 668
- IMPLEMENTATION: 474 / 449
- NONSATD: 3432 / 3272
- DEFECT: 220 / 210
- TEST: 78 / 74
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
snapshot_commit

## 既知の制約

- `repo_missing`: GitHub 上で削除/非公開になったリポジトリ (再取得は Software
  Heritage Vault で可能)。
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
python3 scripts/04_export.py       # CSV / レポート
python3 scripts/05_verify.py --full  # 全行のテキスト検証
python3 scripts/06_blame.py 8431   # 個別の来歴と blame
```
