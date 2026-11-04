# ModernBERT による SATD 検出

`MaldonadoSATD` の `research/finetune_bert.py` と同じ条件で、ModernBERT
（`answerdotai/ModernBERT-base`）を微調整する実験です。コメント本文を主入力にし、
コミットログやコード断片を追加したビューも扱い、SATD / 非 SATD の 2 値分類を行います。

## データ

| データ | 行数 | SATD | 内容 |
| --- | ---: | ---: | --- |
| Tesoro | 4,981 | 1,549 (31.1%) | `tesoro/data/tesoro/tesoro_comment.json`。`NONSATD` 以外を SATD とする |
| Maldonado | 61,628 | 4,479 (7.3%) | `MaldonadoSATD/research/data/finetune_input.csv.gz`。導入コミットが取れた行 |

Tesoro は層化 5 分割、Maldonado は既存実験と同じ層化 5 分割です。Maldonado の
既存 ModernBERT-base の結果は F1 **0.9343**（5 分割、3 エポック、最大長 128）で、
`MaldonadoSATD/research/data/finetune/modernbert_random.json` にあります。

## 環境

ModernBERT は `transformers>=4.48` が必要です。Apple Silicon では MPS を使います。

```bash
python3 -m venv --system-site-packages /tmp/mlenv-modernbert
/tmp/mlenv-modernbert/bin/pip install -U 'transformers>=4.48,<5' scikit-learn accelerate
```

## 実行

```bash
# Tesoro 5 分割
/tmp/mlenv-modernbert/bin/python experiments/modernbert_satd/run_modernbert.py \
    --dataset tesoro --folds 5 --epochs 3 --batch 128 \
    --output experiments/modernbert_satd/results/tesoro_modernbert.json \
    --save-predictions experiments/modernbert_satd/results/tesoro_predictions.csv

# Maldonado の 1 分割だけを確認するパイロット
/tmp/mlenv-modernbert/bin/python experiments/modernbert_satd/run_modernbert.py \
    --dataset maldonado --fold 1 --epochs 1 --batch 32 \
    --output experiments/modernbert_satd/results/maldonado_fold1_1ep.json

# 本文＋ログ / 本文＋コードの 2 セグメント入力
/tmp/mlenv-modernbert/bin/python experiments/modernbert_satd/run_modernbert.py \
    --dataset maldonado --pair log --folds 5 --epochs 3 --batch 128 \
    --output experiments/modernbert_satd/results/maldonado_text_log_modernbert.json
/tmp/mlenv-modernbert/bin/python experiments/modernbert_satd/run_modernbert.py \
    --dataset maldonado --pair code --folds 5 --epochs 3 --batch 128 \
    --output experiments/modernbert_satd/results/maldonado_text_code_modernbert.json
```

`--limit` はスモークテスト用で、指定した数の**訓練行だけ**を間引きます。
テスト行は間引きません。

## 設定

- モデル: `answerdotai/ModernBERT-base`（149M パラメータ）
- 最大長: 128、バッチ: 128（Tesoro） / 32（Maldonado パイロット）
- 学習率: 2e-5、エポック: 3、warmup: なし
- 損失: `BCEWithLogitsLoss`、`pos_weight = 負例数 / 正例数`
- 閾値: 0.5（MaldonadoSATD の既存結果と同じ）

## 結果

### Tesoro（5 分割、3 エポック）

`results/tesoro_modernbert.json`:

| 指標 | 平均 |
| --- | ---: |
| Precision | 0.9147 |
| Recall | 0.9413 |
| F1 | **0.9274** |
| AUC | 0.9876 |

| Fold | Precision | Recall | F1 | AUC |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.9148 | 0.9355 | 0.9250 | 0.9835 |
| 2 | 0.8929 | 0.9677 | 0.9288 | 0.9902 |
| 3 | 0.8705 | 0.9323 | 0.9003 | 0.9826 |
| 4 | 0.9570 | 0.9323 | 0.9444 | 0.9941 |
| 5 | 0.9385 | 0.9385 | 0.9385 | 0.9875 |

閾値 0.5 の混同行列は TP 1,458 / FP 138 / TN 3,294 / FN 91 です
（`tesoro_predictions.csv` から集計）。`pos_weight` により再現率寄り
（0.94）で、Precision は 0.91 でした。

### Maldonado（5 分割、3 エポック）

`results/maldonado_modernbert.json`:

| 指標 | 平均 |
| --- | ---: |
| Precision | 0.9322 |
| Recall | 0.9277 |
| F1 | **0.9300** |
| AUC | 0.9958 |

| Fold | Precision | Recall | F1 | AUC |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 0.9347 | 0.9263 | 0.9305 | 0.9965 |
| 2 | 0.9302 | 0.9219 | 0.9260 | 0.9930 |
| 3 | 0.9270 | 0.9208 | 0.9239 | 0.9968 |
| 4 | 0.9353 | 0.9364 | 0.9359 | 0.9965 |
| 5 | 0.9340 | 0.9330 | 0.9335 | 0.9961 |

`MaldonadoSATD` の既存 ModernBERT-base（バッチ 32、5 分割、3 エポック）は
F1 **0.9343**、Precision 0.9485、Recall 0.9205、AUC 0.9949 です。
今回の実験はバッチ 128 と TF32 を使ったため F1 は 0.9300 で、既存値より
0.004 低い結果になりました。分類器としては同程度で、パイプラインの再現を
確認できています。

## ログ・コードとのアンサンブル

`MaldonadoSATD` の OOF 予測を使い、本文・ログ・コードの3ビューを比較しました。
ここで `log` は「本文＋コミットログ」、`code` は「本文＋コード断片」を
2セグメント入力にした ModernBERT で、いずれも同じ行・同じ5分割です。
`ensemble.py` は確率平均と logit 平均を計算し、閾値は0.5のほか、
他フォールドで選んだ閾値を保持フォールドに適用する `honest` も出します。

```bash
python experiments/modernbert_satd/ensemble.py \
    --text  ../MaldonadoSATD/research/data/finetune/modern_text.csv.gz \
    --log   ../MaldonadoSATD/research/data/finetune/modern_log.csv.gz \
    --code  ../MaldonadoSATD/research/data/finetune/modern_code.csv.gz \
    --output results/maldonado_ensemble_existing_all.json
```

| 入力 | F1@0.5 | F1 honest |
| --- | ---: | ---: |
| 本文のみ | 0.9343 | 0.9346 |
| 本文＋ログ | 0.9368 | **0.9390** |
| 本文＋コード | 0.9360 | 0.9369 |
| ログ＋コード | 0.9333 | 0.9351 |
| 本文＋ログ＋コード（確率平均） | 0.9368 | 0.9364 |
| 本文＋ログ＋コード（logit平均） | 0.9375 | 0.9375 |

- **本文＋ログの logit 平均が最も良く、honest F1 0.9390**（本文単体 0.9346 から +0.0044）。
- 3ビューにすると 0.9375 で、2ビューよりわずかに低い。コードを足すと
  ログの利得が薄まる。
- 確率平均より logit 平均の方が安定。単体モデルのスコア分布が違うためです。
- 本文とのスコア相関はログ 0.965、コード 0.970 で、相関の低いログを足したときだけ
  小さな利得が出ています。

今回 batch 128 で `text+log` / `text+code` を再学習すると、単体では
F1 0.9212 / 0.9201 で、既存 OOF（batch 32）より低くなりました。
しかし、今回の本文モデル（0.9299）と組み合わせると次のようになります
（`results/maldonado_ensemble_batch128.json`）。

| 入力 | 単体 F1@0.5 | 単体 honest | 確率平均 honest | logit平均 honest |
| --- | ---: | ---: | ---: | ---: |
| 本文 | 0.9299 | 0.9333 | — | — |
| 本文＋ログ | 0.9212 | 0.9284 | 0.9347 | **0.9370** |
| 本文＋コード | 0.9201 | 0.9251 | 0.9339 | 0.9360 |
| 本文＋ログ＋コード | — | — | 0.9350 | 0.9359 |

batch 128 でも **本文＋ログの logit 平均が最も良く、honest F1 0.9370**
（本文単体 0.9333 から +0.0037）。単体で本文に負けるログ/コードも、
アンサンブルにすると本文を補助します。既存 OOF の表と傾向は一致しています。

## Tesoro の本文・ログ・コード・アンサンブル

Tesoro にはコミットログ列がないため、`out/tesoro_provenance.csv` の
`commit_id`（コメント行の最終更新者）からローカル clone のコミットメッセージを
取得してログビューを作りました。コードビューは `code_context_10` から
コメント行を除いたものです（コメント本文の漏洩を防ぐため）。

```bash
python experiments/modernbert_satd/build_tesoro_views.py
```

`data/tesoro_views.csv.gz` は 4,981 行、1,434 個のユニークなコミット、
コミットが取れない 4 行と空メッセージ 11 行を含みます。
本文・ログ・コードは同じ行順・同じ層化5分割で学習しました。

| 入力 | F1@0.5 | F1 honest |
| --- | ---: | ---: |
| 本文のみ | 0.9272 | 0.9241 |
| ログのみ | 0.5363 | 0.5413 |
| コードのみ | 0.5989 | 0.5995 |
| 本文＋ログ（確率平均） | **0.9293** | **0.9316** |
| 本文＋コード（確率平均） | 0.9270 | 0.9271 |
| 本文＋ログ＋コード（等重み・確率） | 0.9041 | 0.9006 |
| 本文＋ログ＋コード（0.5 / 0.25 / 0.25） | **0.9352** | **0.9360** |

- Tesoro のログ・コードは単体では弱い（F1 0.54 / 0.60）。
- 等重みで3ビューを混ぜると、弱いモデルに引きずられて本文より悪化します。
- **本文を0.5、ログ・コードを0.25ずつにすると honest F1 0.9360** で、
  本文単体の 0.9241 から +0.0119。弱いビューも、重みを下げれば補助になります。
- 重みは評価行で学習せず、本文を2倍にする固定値です。それでも3ビューの
  等重みより良い、という結果です。

## CodeBERT での同一実験

同じ Tesoro の行・分割・ハイパーパラメータ（5分割、3エポック、最大長128、
バッチ128、学習率2e-5）で、モデルだけ `microsoft/codebert-base` に変えました。

| 入力 | F1@0.5 | F1 honest | Recall@0.5 |
| --- | ---: | ---: | ---: |
| 本文のみ | 0.9349 | 0.9318 | 0.9451 |
| ログのみ | 0.5096 | 0.5252 | 0.6927 |
| コードのみ | 0.5954 | 0.6059 | 0.7250 |
| 本文＋ログ（確率平均） | 0.9343 | 0.9282 | 0.9458 |
| 本文＋コード（確率平均） | **0.9388** | 0.9345 | 0.9561 |
| 本文＋ログ＋コード（等重み） | 0.9188 | 0.9331 | 0.9567 |
| **本文＋ログ＋コード（0.5 / 0.25 / 0.25）** | **0.9389** | **0.9383** | **0.9516** |

ModernBERT との比較（重み付き3ビュー）:

| モデル | 本文 F1 | 3ビュー F1 | 3ビュー honest | 3ビュー Recall |
| --- | ---: | ---: | ---: | ---: |
| ModernBERT-base | 0.9272 | 0.9352 | 0.9360 | 0.9496 |
| CodeBERT-base | **0.9349** | **0.9389** | **0.9383** | **0.9516** |

- CodeBERT の本文モデルは ModernBERT より **+0.0077**（0.9349 vs 0.9272）。
- 重み付き3ビューでも、honest F1 では CodeBERT が **+0.0023** 上回りました。
- ログ・コード単体は CodeBERT でも弱く（0.51 / 0.60）、等重みの3ビューは
  本文より悪化します。重みを下げた3ビューだけが本文を上回ります。
- CodeBERT のログは ModernBERT より低い（0.5096 vs 0.5363）一方、
  コードはほぼ同じ（0.5954 vs 0.5989）でした。

## P0: プロジェクト分割・重複排除分割

ランダム層化5分割は、同一プロジェクトのコメントや重複本文が訓練とテストに
跨がるため、性能を楽観的に見積もる可能性があります。Tesoro で次の2つを追加しました。

- **project split**: 974 プロジェクトを `GroupKFold(5)` で5つのグループに分け、
  プロジェクトが訓練とテストに跨がらないようにする。
- **duplicate-free split**: 空白を正規化した本文をグループとして `GroupKFold(5)`。
  同一本文が別フォールドに跨がらないようにする。

どちらもモデルの seed 0 / 1 / 2 で3回ずつ学習し、平均と95%信頼区間
（t分布、df=2）を出しました。

```bash
python experiments/modernbert_satd/run_modernbert.py \
    --dataset tesoro --input text --csv experiments/modernbert_satd/data/tesoro_views.csv.gz \
    --split project --folds 5 --epochs 3 --batch 128 --seed 0 \
    --model microsoft/codebert-base \
    --output experiments/modernbert_satd/results/p0/codebert_project_seed0.json

python experiments/modernbert_satd/summarize_p0.py
```

| 分割 | CodeBERT F1 | ModernBERT F1 |
| --- | ---: | ---: |
| random（既存） | 0.9349 | 0.9274 |
| project（3 seed） | **0.9277 ± 0.0032** | **0.9145 ± 0.0043** |
| duplicate-free（3 seed） | **0.9330 ± 0.0015** | **0.9177 ± 0.0157** |

- project split で CodeBERT は -0.0072、ModernBERT は -0.0129。
- duplicate-free split で CodeBERT は -0.0019、ModernBERT は -0.0097。
- **CodeBERT の優位は分割を厳しくしても残る**。差は project で +0.0132
  （95% CI ±0.0033）、duplicate-free で +0.0152（±0.0169）。
- ModernBERT は duplicate-free の seed 間分散が大きく、3 seed では
  0.9105〜0.9223 と幅があります。

この結果から、Tesoro のランダム分割は極端に楽観ではありませんが、
プロジェクト横断では CodeBERT でも 0.93 を切ります。次の比較は
project split を標準にすべきです。

## P1: エンコーダ比較（project split）

P0 の project split（974 プロジェクト、5グループ、3シード）を標準にして、
次のエンコーダを同じ条件で比較しました。GraphCodeBERT / UnixCoder はバッチ128、
ModernBERT-large は24GB GPUに収めるためバッチ64です。

| モデル | Precision | Recall | F1 | AUC | CodeBERTとの差 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CodeBERT-base | 0.9180 | 0.9379 | 0.9277 ± 0.0032 | 0.9873 | — |
| ModernBERT-base | 0.9083 | 0.9220 | 0.9145 ± 0.0043 | 0.9843 | -0.0132 |
| GraphCodeBERT-base | 0.9144 | 0.9456 | 0.9295 ± 0.0080 | 0.9886 | +0.0018 |
| UnixCoder-base | 0.8430 | 0.9344 | 0.8860 ± 0.0027 | 0.9784 | -0.0417 |
| **ModernBERT-large** | **0.9421** | 0.9372 | **0.9393 ± 0.0096** | **0.9885** | **+0.0116** |

- **ModernBERT-large が最良（F1 0.9393）**。CodeBERT より +0.0116。
- GraphCodeBERT は CodeBERT とほぼ同等（+0.0018、信頼区間内）。
- UnixCoder は今回の設定では大きく劣る（0.8860）。トークナイザ・学習率・
  mean pooling の相性を切り分ける必要があります。
- 「コード専用モデルが常に強い」とは単純に言えず、**モデル規模と事前学習の
  組み合わせ**が効いています。

```bash
python experiments/modernbert_satd/summarize_p1.py
```

`results/p1/` に各 seed の JSON / predictions、`results/p1_summary.json` に
集計があります。CodeT5+ は encoder-decoder で、現行の
`AutoModelForSequenceClassification` では分類ヘッドが合わないため、
encoder pooling の実装後に追加します。

## P2: 凍結埋め込み＋ロジスティック回帰

P1 と同じ project split で、埋め込みモデルを特徴抽出器として固定し、
ロジスティック回帰（`class_weight='balanced'`）で分類しました。
埋め込みは mean pooling、L2 正規化、最大長256です。

| モデル | F1 | F1 honest | Precision | Recall | AUC |
| --- | ---: | ---: | ---: | ---: | ---: |
| Salesforce/SFR-Embedding-Code-400M_R | **0.8907** | 0.8876 | 0.8720 | 0.9103 | 0.9727 |
| Qwen/Qwen3-Embedding-0.6B（mean） | 0.8848 | 0.8874 | 0.8636 | 0.9070 | 0.9748 |
| jina-code-embeddings-1.5b | 0.8407 | 0.8476 | 0.8054 | 0.8793 | 0.9561 |
| jina-code-embeddings-0.5b | 0.8331 | 0.8400 | 0.7979 | 0.8715 | 0.9547 |
| Qwen3-Embedding-0.6B（last） | 0.7726 | 0.7732 | 0.7209 | 0.8321 | 0.9181 |

- 凍結埋め込みの最良は **SFR-Embedding-Code-400M の 0.8907**。
  fine-tuning した ModernBERT-large（0.9393）や CodeBERT（0.9277）には
  約0.04〜0.05 及びません。
- コード専用の SFR と汎用の Qwen3 はほぼ同等（0.8907 vs 0.8848）。
  今回のタスクでは「コード専用かどうか」よりモデル規模・学習目的の影響が大きい。
- jina-code は 1.5b が 0.5b をわずかに上回る程度（+0.0076）。
- Qwen3 は mean pooling が last pooling より大きく良い（0.8848 vs 0.7726）。
  モデルカードの推奨とタスク特性が一致しないため、pooling は必ず確認する。

```bash
python experiments/modernbert_satd/embedding_eval.py \
    --model Salesforce/SFR-Embedding-Code-400M_R --trust-remote-code \
    --output experiments/modernbert_satd/results/p2/sfr_code_400m.json

python experiments/modernbert_satd/summarize_p2.py
```

CodeSage v2 は `transformers 4.57` で `Conv1D` の import に失敗するため
今回は除外しました（`modeling_codesage.py` が旧APIに依存）。

## P3: エラー分析（ModernBERT-large、project split、seed 0）

最良モデル ModernBERT-large の OOF 予測（seed 0）を、Tesoro の分類・プロジェクト・
コメント長・CodeBERT との差分で分解しました。全体の混同行列は
**TP 1,463 / FP 89 / FN 86 / TN 3,343**、Precision 0.9427、Recall 0.9445、
F1 0.9436 です（フォールド平均は 0.9437）。

### SATD種別ごとの Recall

| 分類 | 件数 | Recall |
| --- | ---: | ---: |
| DOCUMENTATION | 80 | 0.9875 |
| TEST | 78 | 0.9744 |
| IMPLEMENTATION | 474 | 0.9641 |
| DEFECT | 220 | 0.9409 |
| DESIGN | 697 | **0.9240** |
| NONSATD（specificity） | 3,432 | 0.9741 |

**DESIGN の Recall が最も低い**。設計上の意図やトレードオフを述べるコメントは、
表面の TODO/FIXME パターンだけでは拾いにくいことを示します。

### コメント長

| 語数 | 件数 | F1 | Recall |
| --- | ---: | ---: | ---: |
| 0–4 | 1,188 | 0.9416 | 0.9627 |
| 5–9 | 1,596 | 0.9519 | 0.9473 |
| 10–19 | 1,286 | **0.9614** | 0.9632 |
| 20–39 | 592 | 0.9151 | 0.9132 |
| 40–79 | 252 | 0.8901 | 0.9000 |
| 80+ | 67 | 0.8727 | 0.8571 |

長いコメントは文脈が増える一方、ノイズも増えて F1 が下がります。

### プロジェクト別

SATD が5件以上あるプロジェクトでは、最下位でも F1 0.7143
（`tdc22/JAwesomeEngine`、6 SATD）、0.7692（`Narflex/sagetv`、8 SATD）でした。
SATD が1〜2件しかないプロジェクトの F1 0 は標本効果が大きく、モデルの失敗とは
解釈できません。

### CodeBERT との比較

- ModernBERT-large が正しく CodeBERT が誤り: **103件**
- CodeBERT が正しく ModernBERT-large が誤り: **56件**
- 両方正解: 4,703件 / 両方誤り: 119件

ModernBERT-large の優位は、主に CodeBERT が落とす行を拾っているためです。

### 誤りの例

- **False Positive**: `//TODO: change filename param to INT` のような TODO が
  NONSATD と注釈されている例。SATD の境界（将来の変更意図をどこまで含めるか）に
  関するアノテーション揺れの可能性があります。
- **False Negative**: `// We only expect two values...` や
  `// this method supports only guest network creation` のような設計意図の記述。
  明示的な TODO マーカーがないため、DESIGN の Recall 低下に対応しています。

```bash
python experiments/modernbert_satd/analyze_p3.py
```

`results/p3_error_analysis.json` に、混同行列・分類別・プロジェクト別・長さ別・
FP/FN の上位例・CodeBERT との比較を保存しています。

## P3b: クロスデータセット・プロジェクト統計・アノテーション監査

### Maldonado ↔ Tesoro（zero-shot）

プロジェクトの重複はありません（Tesoro 974 / Maldonado 10、共通0）。
訓練データのラベルだけで学習し、相手データをそのまま評価しました。
`best-thr` は評価先のラベルで閾値を選んだ**上限値**で、実運用の値ではありません。

| 訓練 → 評価 | モデル | F1@0.5 | best-thr F1 | Recall@0.5 |
| --- | --- | ---: | ---: | ---: |
| Maldonado → Tesoro | ModernBERT-large | **0.9190** | 0.9259 | 0.8903 |
| Maldonado → Tesoro | CodeBERT | 0.9184 | 0.9244 | 0.9193 |
| Tesoro → Maldonado | ModernBERT-large | **0.8536** | 0.8626 | 0.8480 |
| Tesoro → Maldonado | CodeBERT | 0.8198 | 0.8650 | 0.8852 |

- 大きい Maldonado（61,628行）から Tesoro への転移は 0.92 前後と高い。
- 小さい Tesoro（4,981行）から Maldonado への転移は 0.82〜0.85 に落ちる。
- **訓練データ量とドメインの非対称性**が、モデル差より大きく効いています。
- ModernBERT-large は 0.5 で Precision 0.9497 / Recall 0.8903 と高精度寄り。
  閾値を下げると F1 0.9259 まで上がるため、確率校准のずれもあります。

### プロジェクト別の統計

SATD が5件以上・10行以上ある **30プロジェクト**で、プロジェクト単位の F1 を
計算し、行のブートストラップ（1,000回）で95%区間を出しました。

- ModernBERT-large のプロジェクト F1 中央値 **1.000**、IQR **[0.9126, 1.000]**。
- F1 < 0.8 は **2プロジェクト**のみ。
- 最下位: `tdc22/JAwesomeEngine` 0.7143（6 SATD）、
  `Narflex/sagetv` 0.7692（8 SATD）。
- CodeBERT とのプロジェクト単位の差は、平均 +0.0113、中央値 0。
  ModernBERT-large が勝つ 5、CodeBERT が勝つ 2、Wilcoxon 符号順位検定
  **p=0.398** で、プロジェクト数30では有意差とは言えません。

### FP/FN のアノテーション監査（予備）

175件の誤り（FP 89 / FN 86）を、表層手がかりでスクリーニングしました。

| 誤り | カテゴリ | 件数 |
| --- | --- | ---: |
| FP | NONSATD なのに TODO/FIXME を含む | 33 |
| FP | 不確実性の表現を含む | 9 |
| FP | 設計意図の表現を含む | 8 |
| FP | 欠陥語を含む | 6 |
| FP | 明示的な手がかりなし | 33 |
| FN | 設計意図の暗黙記述 | 14 |
| FN | 欠陥の暗黙記述 | 10 |
| FN | 不確実性の暗黙記述 | 12 |
| FN | 手がかりなし | 50 |

- FP の **33/89 は TODO/FIXME を含む**。SATD の境界（将来変更の意図を
  NONSATD に含めるか）に関するアノテーション揺れの候補です。ただし、
  マーカーがあるだけで SATD とは限らないため、再ラベルはしていません。
- FN の **50/86 は明示的な手がかりがない**。設計意図や暗黙の前提を読む必要があり、
  DESIGN の Recall 0.924 と整合します。
- 全175件は `results/p3_annotation_audit.csv` に保存しました。カテゴリは
  ヒューリスティックな予備分類で、最終的な注釈判断ではありません。

```bash
python experiments/modernbert_satd/project_stats.py
python experiments/modernbert_satd/annotation_audit.py
python experiments/modernbert_satd/summarize_cross.py
```

## 実行環境

- 学習: `z690`（NVIDIA RTX 4090 24GB、CUDA）
- PyTorch 2.9.1+cu128 / transformers 4.57.6 / scikit-learn 1.9.1
- Apple Silicon では MPS でも同じスクリプトが動きます（Tesoro の予備実行で確認）
