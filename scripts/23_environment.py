#!/usr/bin/env python3
"""Record the toolchain used for a run (out/environment.txt)."""
from __future__ import annotations

import datetime
import platform
import subprocess
import sys

from common import OUT


def sh(*args):
    p = subprocess.run(args, capture_output=True, text=True)
    return p.stdout.strip()


def main() -> int:
    lines = [
        f"date: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"python: {sys.version.split()[0]} ({platform.platform()})",
        f"tree-sitter: {sh(sys.executable, '-m', 'pip', 'show', 'tree-sitter').split('Version:')[-1].splitlines()[0].strip() if sh(sys.executable, '-m', 'pip', 'show', 'tree-sitter') else 'unknown'}",
        f"tree-sitter-java: {sh(sys.executable, '-m', 'pip', 'show', 'tree-sitter-java').split('Version:')[-1].splitlines()[0].strip() if sh(sys.executable, '-m', 'pip', 'show', 'tree-sitter-java') else 'unknown'}",
        f"git: {sh('git', '--version')}",
        f"commit: {sh('git', 'rev-parse', 'HEAD')}",
        f"manifest: {sh('git', 'hash-object', str(OUT / 'repo_manifest.csv'))}",
    ]
    (OUT / "environment.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
