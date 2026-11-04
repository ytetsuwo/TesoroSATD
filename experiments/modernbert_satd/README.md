# ModernBERT による SATD 検出

`MaldonadoSATD` の `research/finetune_bert.py` と同じ条件で、ModernBERT
（`answerdotai/ModernBERT-base`）を微調整する実験です。コメント本文だけを入力にし、
SATD / 非 SATD の 2 値分類を行います。

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

## 実行環境

- 学習: `z690`（NVIDIA RTX 4090 24GB、CUDA）
- PyTorch 2.9.1+cu128 / transformers 4.57.6 / scikit-learn 1.9.1
- Apple Silicon では MPS でも同じスクリプトが動きます（Tesoro の予備実行で確認）
