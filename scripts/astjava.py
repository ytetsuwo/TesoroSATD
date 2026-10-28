"""Java AST helpers built on tree-sitter.

The brace-counting heuristic in `14_check_span.py` mistakes braces inside
strings, comments and lambdas for the end of a method. Parsing the file gives
exact method spans, the javadoc block that belongs to each declaration, and the
comment nodes themselves, so a mapped line can be checked against the syntax
tree instead of against a guessed window.

`parse` returns a `Tree` that keeps the source bytes: tree-sitter node offsets
are relative to the whole file, while the root node itself starts at the first
token (leading whitespace excluded), so slicing with `root.text` would shift
every offset.

All lines are 1-based, matching the rest of the pipeline.
"""
from __future__ import annotations

from functools import lru_cache

import tree_sitter_java
from tree_sitter import Language, Parser

LANGUAGE = Language(tree_sitter_java.language())

METHOD_TYPES = ("method_declaration", "constructor_declaration")
COMMENT_TYPES = ("line_comment", "block_comment")
NEST_TYPES = METHOD_TYPES + ("class_declaration", "interface_declaration",
                             "enum_declaration", "record_declaration",
                             "anonymous_class_body", "annotation_type_declaration")


class Tree:
    """A parse tree plus the exact bytes it was built from."""

    __slots__ = ("root", "src")

    def __init__(self, root, src: bytes):
        self.root = root
        self.src = src

    def __repr__(self) -> str:
        return f"<Java tree {self.root.type} {len(self.src)}B>"


@lru_cache(maxsize=1)
def parser() -> Parser:
    return Parser(LANGUAGE)


def parse(src: bytes) -> Tree:
    """Parse a Java compilation unit (error tolerant)."""
    return Tree(parser().parse(src).root_node, src)


def text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def line1(node, src: bytes, edge: str = "start") -> int:
    """1-based line of a node's start or end, matching `str.splitlines`.

    Counting line breaks on bytes would misread UTF-8 continuation bytes
    (0x85, 0x0b, 0x0c, 0x1c-0x1e) as separators, so decode first.
    """
    off = node.start_byte if edge == "start" else node.end_byte
    text = src[:off].decode("utf-8", "replace")
    # A prefix that stops mid-line already counts that line, while one that
    # stops right after a break does not: the sentinel makes both cases one.
    return len((text + "x").splitlines())


def walk(node):
    """Every node, parents before children."""
    yield node
    for child in node.children:
        yield from walk(child)


def methods(tree: Tree) -> list[dict]:
    """All method and constructor declarations, with their exact spans.

    Each entry carries the declaration span (annotations included), the body
    span (None for abstract/native methods) and the nesting depth, so a fragment
    inside an anonymous class can be told apart from the enclosing method.
    """
    src = tree.src
    out = []

    def visit(node, depth: int):
        for child in node.children:
            if child.type in METHOD_TYPES:
                name = child.child_by_field_name("name")
                params = child.child_by_field_name("parameters")
                body = child.child_by_field_name("body")
                ptext = text(params, src) if params else "()"
                out.append({
                    "type": child.type,
                    "name": text(name, src) if name else None,
                    "n_params": 0 if ptext == "()" else ptext.count(",") + 1,
                    "depth": depth,
                    "start": line1(child, src),
                    "end": line1(child, src, "end"),
                    "body_start": line1(body, src) if body else None,
                    "body_end": line1(body, src, "end") if body else None,
                    "abstract": body is None,
                })
            visit(child, depth + 1 if child.type in NEST_TYPES else depth)

    visit(tree.root, 0)
    return out


def enclosing(tree: Tree, line: int, methods_cache=None) -> dict | None:
    """Innermost method whose declaration..close spans `line` (1-based)."""
    cands = [m for m in (methods_cache if methods_cache is not None else methods(tree))
             if m["start"] <= line <= m["end"]]
    if not cands:
        return None
    # innermost = deepest, then the shortest span
    return max(cands, key=lambda m: (m["depth"], -((m["end"] - m["start"]))))


def locate_method(tree: Tree, line: int, slack: int = 8):
    """Innermost method containing `line`, or the nearest declaration within `slack`.

    The aligned start of a dataset function can fall inside a method body (the
    dataset snippet is a fragment) or a few lines above a declaration (a comment
    line). Both mean "this method", so look around before giving up.
    """
    ms = methods(tree)
    m = enclosing(tree, line, ms)
    if m is None:
        near = [x for x in ms if abs(x["start"] - line) <= slack]
        m = min(near, key=lambda x: abs(x["start"] - line)) if near else None
    return m


def comments(tree: Tree) -> list[dict]:
    """Every comment node with its line span and text."""
    src = tree.src
    return [{"start": line1(n, src), "end": line1(n, src, "end"),
             "kind": "line" if n.type == "line_comment" else "block",
             "doc": text(n, src).startswith("/**"),
             "text": text(n, src)}
            for n in walk(tree.root) if n.type in COMMENT_TYPES]


def comment_lines(tree: Tree) -> list[bool]:
    """1-based indexable mask: True when the line is (part of) a comment."""
    n = len(tree.src.decode("utf-8", "replace").splitlines()) + 1
    mask = [False] * (n + 1)
    for c in comments(tree):
        for ln in range(c["start"], c["end"] + 1):
            if ln < len(mask):
                mask[ln] = True
    return mask


def doc_block(tree: Tree, method: dict) -> list[dict]:
    """Comment nodes forming the javadoc/comment block directly above a method.

    Annotations are part of the method node, so only comments (and blank lines,
    which are not nodes) may sit between the block and the declaration.
    """
    src = tree.src
    target = None
    for n in walk(tree.root):
        if n.type in METHOD_TYPES and line1(n, src) == method["start"] \
                and line1(n, src, "end") == method["end"]:
            target = n
            break
    if target is None or target.parent is None:
        return []
    sibs = target.parent.children
    i = sibs.index(target)
    out, pos = [], target.start_byte
    for j in range(i - 1, -1, -1):
        n = sibs[j]
        if n.type not in COMMENT_TYPES:
            break
        if src[n.end_byte:pos].strip():
            break
        out.append({"start": line1(n, src), "end": line1(n, src, "end"),
                    "kind": "line" if n.type == "line_comment" else "block",
                    "doc": text(n, src).startswith("/**")})
        pos = n.start_byte
    return list(reversed(out))


def errors(tree: Tree) -> list[dict]:
    """Syntax errors tree-sitter recovered from (line + snippet of the node)."""
    return [{"line": line1(n, tree.src), "type": n.type,
             "text": text(n, tree.src)[:80]}
            for n in walk(tree.root) if n.type == "ERROR" or n.is_missing]
