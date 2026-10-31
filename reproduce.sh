#!/bin/sh
# Full reproduction: pin the clones, rebuild every stage, verify.
set -e
cd "$(dirname "$0")"
python3 scripts/00_pin_repos.py --check
python3 scripts/03_locate.py --all
python3 scripts/08_history.py --remap
python3 scripts/16_symbol_history.py
python3 scripts/17_history_text.py
python3 scripts/18_reshape.py
python3 scripts/21_crossfile.py
python3 scripts/25_manual_fixes.py
python3 scripts/04_export.py
python3 scripts/05_verify.py --all-checks
python3 scripts/27_strict_audit.py
python3 scripts/04_export.py
python3 scripts/24_consistency.py
python3 scripts/23_environment.py
