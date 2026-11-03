# SATD 導入コミットの特定

`out/tesoro_provenance.csv` の `commit_id` は、コメント本文行の **最後の更新者** である。
このパスは、現在のコメント本文が最初に現れたコミットを別に求め、
`out/tesoro_introduced.csv` に記録する。元の来歴 CSV は変更しない。

## 対象

- `introduced_in`: 来歴付きコメントのうち、`27_strict_audit.py` の本文検査が
  `confirmed` のもの。
- `dataset_introduced_in`: 上記以外も含め、dataset 側の本文を履歴から探したもの。
  完全一致を優先し、無い場合は `near_exact` / `prefix` の参考候補も別ラベルで記録する。
  現在の本文と異なる場合は、現在の本文の導入とは別の列に記録する。
- 本文が不一致、要確認、来歴なしの行は `introduced_in` を確定しない。
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
6. 現在の本文が確定していない行は、dataset 本文を `git log -S` で履歴から探し、
   完全一致するコメントノードがある最も古いコミットから親方向へ確認する。
   - `historical`: 完全一致の導入点が1つ
   - `ambiguous`: 独立した完全一致の系統が複数
   - `candidate`: 完全一致はあるが、親探索が上限に達した
7. 完全一致が無い場合は、現在のコメント本文との類似度が 0.95 以上のときだけ
   `near_exact`、dataset 本文が現在の長いコメントの接頭辞であるときは `prefix`
   として参考候補を記録する。
8. 記号だけ・空のコメントは、本文ではなくコメントノードと前後のコード行を
   親方向へたどり、`context` として代理の導入コミットを記録する。
   備考に「本文ではなく周辺コードから推定した代理値」と明記する。
9. 短い一般語は `not_informative`、履歴に無ければ `not_found` とする。

## 制約

- `introduced_in` は **現在の本文** を導入したコミットである。過去に文言が
  書き換えられていれば、書き換え後の本文を導入したコミットであり、
  SATD コメント全体の最初の導入コミットとは限らない。
- `dataset_introduced_in` の `historical` は dataset 本文の完全一致であり、
  `candidate` / `ambiguous` / `near_exact` / `prefix` は確定ではない。現在の本文の
  `introduced_in` とは別の列として扱う。
- `context` は記号だけ・空のコメントについて、コメントノードと前後のコード行が
  同じ範囲をたどった代理値である。コメント本文の導入を保証しないため、
  `dataset_note` にその旨を明記する。
- 短い一般語のコメントは本文で起源を決められないため、
  `not_informative` としてコミットを付けない。
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
`basis` は `blame`（本文行の候補から開始）、`version`（スナップショットから開始）、
`pickaxe`（履歴の再探索）のいずれか。
`dataset_basis` は `history-exact` / `history-exact-candidate` / `history-ambiguous` /
`near-exact` / `source-prefix` / `context-lineage` のいずれか。
`dataset_introduced_in` は dataset 本文の履歴で、`dataset_status` が
`current` / `historical` / `candidate` / `ambiguous` / `near_exact` / `prefix` /
`context` のとき値を持つ。
`not_informative` / `not_found` / `unmapped` はコミットを付けない。

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
`--jobs 8` で並列数を指定できる（既定 6）。
`--fail-on ambiguous,truncated,unresolved` を付けると、未確定を品質ゲートにできる。

## 全件結果（2026-11-03）

### 現在の本文

| 状態 | 件数 |
| --- | ---: |
| `introduced` | 4,939 |
| `ambiguous` | 0 |
| `truncated` | 0 |
| `unresolved` | 0 |
| `not_confirmed` | 39 |
| `unmapped` | 4 |
| 合計 | 4,981 |

導入コミットを特定できた 4,939 件のうち、記録済み `commit_id` と同一が 4,542 件、
異なるのが 396 件だった。異なる例は、複数行コメントの完成や、コード整形で
同じ行の blame が更新されたが本文は変わっていないケースである。

### データセット本文

| 状態 | 件数 |
| --- | ---: |
| `current`（現在本文と同一） | 4,939 |
| `historical`（履歴で完全一致） | 8 |
| `candidate`（完全一致、親探索打ち切り） | 2 |
| `ambiguous`（独立した完全一致が複数） | 0 |
| `near_exact`（本文差ありの参考候補） | 1 |
| `prefix`（現在本文の接頭辞） | 1 |
| `context`（周辺コードから推定） | 24 |
| `not_informative`（短語） | 2 |
| `not_found`（履歴に見つからない） | 1 |
| `unmapped`（来歴なし） | 4 |
| 合計 | 4,981 |

`dataset_introduced_in` を付けたのは 4,975 件（うち完全一致 4,947 件）。
完全一致の内訳は、現在本文と同一 4,939 件、履歴で完全一致 8 件。
残りは候補 2 件、近い候補 1 件、接頭辞 1 件、周辺コードから推定 24 件で、
これらは確定ではない。

### 未確定 38 件の内訳

- **履歴で完全一致 8 件**: `15634:7`、`16039:0`、`30631:0`、`16892:0`、
  `18139:0`、`18140:0`、`9251:0`、`3182:0`。
- **履歴候補 2 件**: `21954:3`、`11774:3`。完全一致の本文はあるが、
  親方向の探索が 128 コミットで打ち切られたもの。
- **近い候補 1 件**: `12044:0`。dataset 本文が `sadly ` を余分に含み、
  現在のコメントと完全一致しないため `near_exact` とした。
- **接頭辞 1 件**: `32108:8`。dataset 本文が現在の長いコメントの接頭辞で
  あるため `prefix` とした。
- **周辺コードから推定 24 件**: 記号 17 件（`}`、`*/`、`>>`、`>=` など）と
  空コメント 7 件（`//`、空の `/** */`）。本文では起源を決められないため、
  コメントノードと前後のコード行を親方向へたどった代理値を `context` とし、
  `dataset_note` に「本文ではなく周辺コードから推定した代理値」と記録した。
- **短語 2 件**: `34583:25` の `delete`、`18099:34` の `get`。
  一般語が長い Javadoc に含まれるだけなので `not_informative` とした。

### 行・ファイル対応の修正

本文未確定 66 件の再調査で、同一関数内の完全一致 24 件を正しい行へ再対応した
（`not_confirmed` 66 → 42）。さらに次の 3 件は、別ファイルに同じ本文があった
ため `25_manual_fixes.py` で対応先を修正した（42 → 39）。

- `31506:0` / `31507:0`: `AnimalModelTest.java` → `AvailabilityTest.java` 124–126 / 134–136
- `16008:0`: `EasyAIPlayer.java` → `AIPlayer.java` 29–34

`25_manual_fixes.py` の手動対応付けは 48 件になった。
