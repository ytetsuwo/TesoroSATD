# SATD 導入コミットの特定

`out/tesoro_provenance.csv` の `commit_id` は、コメント本文行の **最後の更新者** である。
このパスは、現在のコメント本文が最初に現れたコミットを別に求め、
`out/tesoro_introduced.csv` に記録する。元の来歴 CSV は変更しない。

## 対象

- 来歴付きコメントのうち、`27_strict_audit.py` の本文検査が `confirmed` のもの。
- 本文が不一致、要確認、来歴なしの行は導入コミットを確定しない。
  `status` はそれぞれ `not_confirmed` / `unmapped` になる。

## アルゴリズム

1. スナップショット（HEAD または `snapshot_commit`）で、コメント本文を構成する
   実質行の blame レコードを取得する。各行の SHA・元ファイル・元行を保持する。
2. 各 SHA の版で、blame が示す元ファイルに **本文全体と順序が一致する
   コメントノード** があるかを AST で確認する。
   - 同一行の複数コメント、連続する行コメント、空行を挟む行コメント、
     同一行の複数ブロックコメントは、dataset の1コメントを構成する場合だけ連結する。
   - 本文の完全一致であり、部分一致では候補にしない。
3. 本文全体が存在する候補のうち、系譜上もっとも新しいコミットから開始する。
   候補が無い場合だけスナップショット自体を使う。
4. 親コミットをたどり、同じパス（リネームは `git diff-tree -M` で追跡）に
   本文全体が残っている限り過去へ進む。親に本文が無くなったコミットが導入点。
5. 打ち切り（`--max-walk`、既定 128）や複数の独立した導入点は、確定にしない。
   - `introduced`: 導入点が1つ
   - `ambiguous`: 独立した導入点が複数
   - `truncated`: 親方向の探索が上限に達した
   - `unresolved`: blame やファイル取得から導入点を決められない

## 制約

- `introduced_in` は **現在の本文** を導入したコミットである。過去に文言が
  書き換えられていれば、書き換え後の本文を導入したコミットであり、
  SATD コメント全体の最初の導入コミットとは限らない。
- 同一本文が別ファイルへコピーされた場合、コピー元の唯一の起源は保証しない。
- マージコミットでは、複数親のうち本文を持つ親だけをたどる。独立した導入が
  複数ある場合は `ambiguous` として候補を残す。
- `recorded_commit` は従来の `commit_id`（本文行の最終更新者）で、変更しない。

## 出力

| ファイル | 内容 |
| --- | --- |
| `out/tesoro_introduced.csv` | コメント単位の導入コミット、日付、作者、状態 |
| `out/introduced.jsonl` | 探索した候補・終端・根拠を含む詳細 |
| `out/introduced_summary.json` | 状態別件数と記録済み `commit_id` との一致数 |
| `out/introduced_summary.md` | 人間向けの集計 |

`introduced_short` / `introduced_url` は GitHub のコミット表示用。
`basis` は `blame`（本文行の候補から開始）または `version`（スナップショットから開始）。

## 実行

```bash
python3 scripts/27_strict_audit.py  # 本文の確定判定を先に作る
python3 scripts/28_introduced.py    # 導入コミットを全件探索
python3 scripts/28_introduced.py --retry-truncated  # 打ち切りだけ pickaxe で再探索
python3 scripts/28_introduced.py --retry-ambiguous  # 複数候補を完全一致と first-parent で解決
python3 scripts/04_export.py        # summary.md に反映（fingerprint 一致時）
python3 scripts/24_consistency.py   # 件数・キー・fingerprint を検査
```

`28_introduced.py --max-walk 256` で探索上限を延ばせる（既定 128）。
`--jobs 8` で並列数を指定できる（既定 4）。
`--fail-on ambiguous,truncated,unresolved` を付けると、未確定を品質ゲートにできる。

## 全件結果（2026-11-03）

| 状態 | 件数 |
| --- | ---: |
| `introduced` | 4,895 |
| `ambiguous` | 0 |
| `truncated` | 0 |
| `unresolved` | 0 |
| `not_confirmed` | 66 |
| `unmapped` | 20 |
| 合計 | 4,981 |

導入コミットを特定できた 4,895 件のうち、記録済み `commit_id` と同一が 4,508 件、
異なるのが 387 件だった。異なる例は、複数行コメントの完成や、コード整形で
同じ行の blame が更新されたが本文は変わっていないケースである。

候補が複数あった 5 件は、本文全体の完全一致と first-parent 系譜で 1 件に絞れた。
`not_confirmed` は本文の厳密検査が確定していないもの、`unmapped` は来歴がないもの。
