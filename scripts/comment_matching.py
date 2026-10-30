"""Comment-text matching, independent of Git and function discovery.

Keep the established exact → structural → fuzzy order and tie-breaking rules.
Line positions returned to callers are 1-based; scan indexes are 0-based.
"""
from __future__ import annotations

import re

from common import alnum, core, mclass, similarity

MARKER_ANY = re.compile(r"//|/\*|\*/|^\s*\*")
FUZZY_MIN = 0.80


def cbody(line: str) -> str:
    """Comment text, with markers and any leading code removed."""
    marker = MARKER_ANY.search(line)
    return MARKER_ANY.sub(" ", line[marker.start():]) if marker else ""


class CommentIndex:
    """Lazy text normalization shared by all comments mapped to one file.

    The index lives only for the current file analysis, never across revisions.
    It does not change AST masks or which candidates are eligible.
    """

    def __init__(self, lines):
        self.lines = lines
        self._cores = {}
        self._bodies = {}

    def core(self, index):
        if index not in self._cores:
            self._cores[index] = core(self.lines[index])
        return self._cores[index]

    def body(self, index):
        if index not in self._bodies:
            self._bodies[index] = cbody(self.lines[index])
        return self._bodies[index]


def candidate_blocks(lines, size, lo, hi, is_comment):
    """Blocks of `size` nonblank comment lines; layout-only blanks are skipped."""
    for start in range(lo, hi):
        if not lines[start].strip():
            continue
        if is_comment is not None and not is_comment[start + 1]:
            continue
        indexes, pos = [], start
        while pos < hi and len(indexes) < size:
            if not lines[pos].strip():
                pos += 1
                continue
            if is_comment is not None and not is_comment[pos + 1]:
                break
            indexes.append(pos)
            pos += 1
        if len(indexes) == size:
            yield indexes


def map_comment(cl, lines, start, end, span=150, used=None, lo=None, hi=None,
                is_comment=None, index=None):
    """Return (1-based line range, exact/structural/fuzzy), or ([], None).

    Explicit bounds override the default window. `used` retains its existing
    fuzzy-only behavior; callers check overlaps separately for relocated matches.
    """
    if not cl:
        return [], None
    if lo is None:
        lo = max(0, start - 1 - span)
    if hi is None:
        hi = min(len(lines), end + span)
    hi = min(hi, len(lines))
    index = index if index is not None else CommentIndex(lines)
    size = len(cl)
    wanted = [core(line) for line in cl]
    exact, structural = [], []
    for indexes in candidate_blocks(lines, size, lo, hi, is_comment):
        got = [index.core(i) for i in indexes]
        equal = sum(1 for want, have in zip(wanted, got) if have and want == have)
        if equal == size:
            exact.append(indexes)
        elif size <= 1 or (mclass(lines[indexes[0]])
                           and mclass(lines[indexes[0]]) == mclass(cl[0])
                           and mclass(lines[indexes[-1]]) == mclass(cl[-1])):
            if size == 1:
                if wanted[0] and wanted[0] in got[0]:
                    structural.append(indexes)
            elif equal >= 1:
                structural.append(indexes)
    for kind, candidates in (("exact", exact), ("structural", structural)):
        if candidates:
            indexes = min(candidates, key=lambda x: abs((x[-1] + 1) - start))
            return list(range(indexes[0] + 1, indexes[-1] + 2)), kind

    want = " ".join(wanted)
    if len(alnum(want)) < 12:
        return [], None
    used = used or set()
    best, pos = None, lo
    while pos < hi:
        # Preserve the legacy used-index convention here; fixing it is a
        # matching-policy change and must be reviewed separately.
        if pos in used or not index.body(pos) or (is_comment is not None and not is_comment[pos + 1]):
            pos += 1
            continue
        end_pos, parts, first, last = pos, [], None, None
        while end_pos < hi and len(parts) <= size + 4:
            body = index.body(end_pos)
            if not body:
                if lines[end_pos].strip():
                    break
                end_pos += 1
                continue
            if first is None:
                first = end_pos
            last = end_pos
            parts.append(body)
            end_pos += 1
        got = " ".join(core(part) for part in parts)
        if got:
            ratio = similarity(want, got)
            if ratio >= FUZZY_MIN and (best is None or ratio > best[0]):
                best = (ratio, first, last)
        pos = end_pos
    if best:
        return list(range(best[1] + 1, best[2] + 2)), "fuzzy"
    return [], None
