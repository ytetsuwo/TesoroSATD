# AGENTS.md

Tesoro データセット（Java コメント 4,981 件 / 関数 1,255 件）の来歴（repo / file / commit）
を特定するパイプライン。`repos/`（100GB 超）は Git 管理外。詳細は `README.md` と
`docs/repo_acquisition.md`。

## セットアップ

```bash
python3 -m pip install -r requirements.txt
python3 scripts/00_pin_repos.py --check   # drifted: 0 が正常
```

## コマンド

```bash
./reproduce.sh                      # 00→03→08→16→17→18→21→25→04→05→24→23
python3 scripts/03_locate.py --all  # 関数の再照合（約 5 分）
python3 scripts/04_export.py        # CSV / summary.md
python3 scripts/05_verify.py --full # 0 bad が正常
python3 scripts/24_consistency.py   # データ・CSV・README・manifest の突き合わせ
```

## データの不変条件

- `out/tesoro_provenance.csv` は 4,981 行、`functions_located.jsonl` は 1,255 関数。
- `status`（関数）と `commit_id`（コメント）は別の粒度。`ok` でもコメント未対応があり得る。
- `at_head=false` は `snapshot_commit` と `path_snapshot` を使う。
- `projectname` は `owner/name`、MSR16 の版は `@sha7`。

## ルール

1. スクリプト番号は振り直さず、新しい段は末尾に追加する。
2. 照合を変えたら 03 → 後段 → 04 → 05 → 24 まで通す。
3. 数値が変わったら README と `out/summary.md` の両方を更新する。
4. 手動対応付けは `scripts/25_manual_fixes.py` に追記する。
5. 閾値未満は未対応のまま残す（誤対応を入れない）。
6. コミットは日本語で件数の変化を含める。

## 落とし穴

- clone の HEAD が動くと結果が変わる。再現前は `00_pin_repos.py --pin`。
- blob:none の部分クローンでは `git log -S` が極端に遅い。
- `repos/` の symlink 6 件と `work/manual_reject.jsonl` は意図的に残している。
- 巨大リポジトリは `--filter=blob:none --sparse` か `--no-checkout`。

## 完了条件

`05 --full` が 0 bad、`05 120` が 120/120、`24` が OK、`00 --check` が drifted 0。
