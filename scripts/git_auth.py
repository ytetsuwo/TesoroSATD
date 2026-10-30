"""Prepare Git authentication without repeatedly launching the GitHub CLI."""
import os
import subprocess
from functools import lru_cache
from threading import Lock

_token_lock = Lock()


@lru_cache(maxsize=1)
def _cli_token():
    return subprocess.run(["gh", "auth", "token"], capture_output=True,
                          text=True).stdout.strip()


def git_env():
    """Fresh environment; CLI fallback token is cached only for this process.

    GH_TOKEN is checked on each call. The lock prevents simultaneous workers
    from launching several CLI lookups before the first result is cached.
    Tokens stay in memory and subprocess environments, never in report files.
    """
    token = os.environ.get("GH_TOKEN")
    if not token:
        with _token_lock:
            token = _cli_token()
    env = os.environ.copy()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"url.https://x-access-token:{token}@github.com/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env
