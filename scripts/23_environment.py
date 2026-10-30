#!/usr/bin/env python3
"""Record the toolchain used for a run (out/environment.txt)."""
from __future__ import annotations

import datetime
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version

from common import OUT


def sh(*args):
    p = subprocess.run(args, capture_output=True, text=True)
    return p.stdout.strip()


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def main() -> int:
    lines = [
        f"date: {datetime.datetime.now().isoformat(timespec='seconds')}",
        f"python: {sys.version.split()[0]} ({platform.platform()})",
        f"tree-sitter: {package_version('tree-sitter')}",
        f"tree-sitter-java: {package_version('tree-sitter-java')}",
        f"git: {sh('git', '--version')}",
        f"commit: {sh('git', 'rev-parse', 'HEAD')}",
        f"manifest: {sh('git', 'hash-object', str(OUT / 'repo_manifest.csv'))}",
    ]
    (OUT / "environment.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
