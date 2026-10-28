"""Export the located functions as CSVs (Maldonado style + provenance).

Inputs : out/functions_located.jsonl, tesoro/data/tesoro/tesoro_comment.json
Outputs: out/tesoro_msr16.csv            projectname,classification,commenttext
         out/tesoro_msr16_versioned.csv  same, projectname = owner/name@<short sha>
         out/tesoro_provenance.csv       one row per comment with commit info
         out/summary.md                  coverage report
"""
from __future__ import annotations

import csv
import datetime
import json
from collections import Counter

from common import OUT, load_tesoro

# Maldonado's label for a comment without technical debt; Tesoro calls it NONSATD
LABEL = {"NONSATD": "WITHOUT_CLASSIFICATION"}


def blame_url(repo, sha, path, line):
    if not (sha and path and line):
        return ""
    return f"https://github.com/{repo}/blame/{sha}/{path}#L{line}"


def get(url):
    return f"https://github.com/{url}/commit/" if url else ""


def main():
    funcs, comments = load_tesoro()
    located = {}
    for line in (OUT / "functions_located.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            located[r["id"]] = r

    rows = []
    for fid, recs in comments.items():
        loc = located.get(fid, {})
        by_cid = {c["comment_id"]: c for c in loc.get("comments", [])}
        for r in recs:
            c = by_cid.get(r["comment_id"], {})
            lines = c.get("lines") or []
            first = lines[0] if lines else None
            sha = c.get("commit") or ""
            when = c.get("author_time")
            path = loc.get("path", "")
            path_at = c.get("path_at") or loc.get("path_at_commit") or path
            repo = loc.get("repo") or funcs[fid]["repo"]
            rows.append({
                "function_id": fid,
                "comment_id": r["comment_id"],
                "projectname": repo,
                "classification": r["classification"],
                "commenttext": r["comment"],
                "repo": repo,
                "path": path,
                "path_at_commit": path_at,
                "start_line": loc.get("start", ""),
                "comment_line": first or "",
                "n_comment_lines": len(lines),
                "commit_id": sha,
                "commit_short": sha[:10],
                "commit_url": get(repo) + sha if sha else "",
                "blame_url": blame_url(repo, sha, path_at, first),
                "commit_author": c.get("author") or "",
                "commit_date": (when if isinstance(when, str)
                                else datetime.date.fromtimestamp(when).isoformat()
                                if when else ""),
                "match": c.get("match") or "",
                "function_ratio": loc.get("ratio", ""),
                "status": loc.get("status", "unknown"),
                # a marker-only comment recovered from history carries its own
                # revision, so the row points at that snapshot, not at HEAD
                "at_head": loc.get("status") == "ok" and not c.get("at_commit"),
                "snapshot_commit": c.get("at_commit") or loc.get("commit_sha", ""),
            })

    rows.sort(key=lambda x: (x["projectname"], x["function_id"], x["comment_id"]))

    with open(OUT / "tesoro_provenance.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Maldonado-compatible (msr16) views
    with open(OUT / "tesoro_msr16.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["projectname", "classification", "commenttext"])
        for r in rows:
            w.writerow([r["projectname"], LABEL.get(r["classification"], r["classification"]),
                        r["commenttext"]])

    with open(OUT / "tesoro_msr16_versioned.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["projectname", "classification", "commenttext"])
        for r in rows:
            name = (f"{r['projectname']}@{r['commit_short'][:7]}" if r["commit_short"]
                    else r["projectname"])
            w.writerow([name, LABEL.get(r["classification"], r["classification"]),
                        r["commenttext"]])

    # ------------------------------------------------------------- report
    n_func = len(located)
    stat = Counter(r["status"] for r in located.values())
    conf = Counter(r.get("confidence") for r in located.values()
                   if r["status"] in ("ok", "ok_history"))
    n_rows = len(rows)
    withsha = sum(1 for r in rows if r["commit_id"])
    nfunc_sha = sum(1 for r in located.values() if r.get("commit"))
    n_hist = stat.get("ok_history", 0)
    match = Counter(r["match"] for r in rows if r["commit_id"])
    dates = [r["commit_date"] for r in rows if r["commit_date"]]
    cls = Counter(r["classification"] for r in rows)
    lcls = Counter(r["classification"] for r in rows if r["commit_id"])
    n_repo = len({r["repo"] for r in rows if r["commit_id"]})

    lines = [
        "# Tesoro provenance (repo / file / commit)",
        "",
        f"生成: {datetime.datetime.now():%Y-%m-%d %H:%M}",
        "",
        "## カバレッジ",
        "",
        f"- 関数: {n_func} 件中 {stat.get('ok', 0) + stat.get('ok_history', 0)} 件を特定 "
        f"({(stat.get('ok', 0) + n_hist) / n_func:.1%})、うち commit 取得 {nfunc_sha} 件"
        f" (履歴から復元 {n_hist} 件)",
        f"- コメント: {n_rows} 件中 {withsha} 件に commit を付与 "
        f"({withsha / n_rows:.1%})、{n_repo} プロジェクト",
        f"- 関数一致度: {dict(conf)}",
        f"- コメント特定方法: {dict(match)} (exact=本文一致, structural=行末コメント・部分一致, fuzzy=本文が改変, position=記号のみ, relocated=別メソッドへ移動)",
        f"- commit 日付範囲: {min(dates) if dates else '-'} 〜 {max(dates) if dates else '-'}",
        "",
        "## 状態",
        "",
    ]
    for k, v in stat.most_common():
        lines.append(f"- {k}: {v}")
    lines += ["", "## ラベル分布 (全件 / commit あり)", ""]
    for k in cls:
        lines.append(f"- {k}: {cls[k]} / {lcls.get(k, 0)}")
    lines += [
        "",
        "## ファイル",
        "",
        "| ファイル | 行 | 内容 |",
        "| --- | --- | --- |",
        f"| `out/tesoro_provenance.csv` | {n_rows} | コメント単位の来歴 (下記の列) |",
        f"| `out/tesoro_msr16.csv` | {n_rows} | Maldonado MSR16 形式 (3 列) |",
        f"| `out/tesoro_msr16_versioned.csv` | {n_rows} | 同上、projectname に commit を付加 |",
        "",
        "`tesoro_provenance.csv` の列: function_id, comment_id, projectname,",
        "classification, commenttext, repo, path, path_at_commit, start_line,",
        "comment_line, n_comment_lines, commit_id, commit_short, commit_url,",
        "blame_url, commit_author, commit_date, match, function_ratio, status, at_head,",
        "snapshot_commit",
        "",
        "## 既知の制約",
        "",
        "- `repo_missing`: GitHub 上で削除/非公開になったリポジトリ (再取得は Software",
        "  Heritage Vault で可能)。",
        "- `ok_history`: HEAD には無いが、履歴 `git log -S` で見つけたもの。",
        "  `path` と行番号はその commit 時点のもの (`at_head=false`)。",
        "- `not_at_head` / `weak_match`: 履歴を探しても見つからなかったもの。",
        "- `commit_id` はコメント行の blame なので、関数本体の最終変更とは異なる。",
        "- `projectname` は `owner/name`。Maldonado の `project-version` に相当する",
        "  版情報は commit で表す (`tesoro_msr16_versioned.csv`)。",
        "- `NONSATD` は msr16 形式では `WITHOUT_CLASSIFICATION` に改名。",
        "",
        "## 再現方法",
        "",
        "```",
        "python3 scripts/01_repo_meta.py    # GitHub API で生死と既定ブランチ",
        "python3 scripts/02_clone_repos.py  # blob なし部分クローン + *.java checkout",
        "python3 scripts/03_locate.py       # 関数アラインメント + git blame",
        "python3 scripts/04_export.py       # CSV / レポート",
        "python3 scripts/05_verify.py --full  # 全行のテキスト検証",
        "python3 scripts/06_blame.py 8431   # 個別の来歴と blame",
        "```",
    ]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
