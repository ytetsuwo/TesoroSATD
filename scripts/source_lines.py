"""Index byte offsets using Python's Unicode ``str.splitlines`` semantics."""
from bisect import bisect_right
import re

# Recognize Unicode separators by their full UTF-8 sequences, never by a
# continuation byte (e.g. 0x85 may also occur inside a Chinese character).
BREAKS = re.compile(rb"\r\n|[\n\r\x0b\x0c\x1c-\x1e]|\xc2\x85|\xe2\x80[\xa8\xa9]")


class LineIndex:
    """Build once per source; resolve each node offset in logarithmic time."""

    def __init__(self, source: bytes):
        # A prefix ending between CR and LF has already crossed a separator.
        self.break_ends = [match.start() + 1 if match.group() == b"\r\n"
                           else match.end() for match in BREAKS.finditer(source)]

    def line(self, offset: int) -> int:
        return bisect_right(self.break_ends, offset) + 1
