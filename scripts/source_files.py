"""Bounded, run-local file cache for provenance verification.

Keys include the repository, snapshot path and revision. HEAD files are cached
only within one verification run; create a new cache after changing a checkout.
"""
from collections import OrderedDict

from common import git


class SourceFiles:
    def __init__(self, maxsize=128):
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self.maxsize = maxsize
        self._files = OrderedDict()

    def read(self, directory, path, revision=None):
        """Return split lines at HEAD (revision=None) or at a Git revision."""
        key = (directory, path, revision)
        if key in self._files:
            self._files.move_to_end(key)
            return self._files[key]
        if revision is None:
            lines = (directory / path).read_text(errors="replace").splitlines()
        else:
            result = git(directory, "show", f"{revision}:{path}")
            if result.returncode != 0:
                raise OSError(result.stderr.strip()[:120])
            lines = result.stdout.splitlines()
        self._files[key] = lines
        if len(self._files) > self.maxsize:
            self._files.popitem(last=False)
        return lines
