"""Indexed Java syntax trees; public helpers are re-exported by astjava."""
from threading import local

import tree_sitter_java
from tree_sitter import Language, Parser

from source_lines import LineIndex

LANGUAGE = Language(tree_sitter_java.language())
METHOD_TYPES = ("method_declaration", "constructor_declaration")
COMMENT_TYPES = ("line_comment", "block_comment")
NEST_TYPES = METHOD_TYPES + ("class_declaration", "interface_declaration",
                             "enum_declaration", "record_declaration",
                             "anonymous_class_body", "annotation_type_declaration")
_parsers = local()


def parser() -> Parser:
    """Reuse one parser per worker, not one mutable parser across workers."""
    if not hasattr(_parsers, "parser"):
        _parsers.parser = Parser(LANGUAGE)
    return _parsers.parser


class Tree:
    """Own the exact source, its line index, and a single traversal of nodes."""

    __slots__ = ("root", "src", "_lines", "_nodes", "_methods", "_method_nodes",
                 "_comments")

    def __init__(self, root, src: bytes):
        self.root = root
        self.src = src
        self._lines = LineIndex(src)
        self._nodes = None
        self._methods = None
        self._method_nodes = {}
        self._comments = None

    def __repr__(self) -> str:
        return f"<Java tree {self.root.type} {len(self.src)}B>"

    def line(self, node, edge="start"):
        offset = node.start_byte if edge == "start" else node.end_byte
        return self._lines.line(offset)

    def nodes(self):
        if self._nodes is None:
            self._nodes = list(walk(self.root))
        return self._nodes


def parse(src: bytes) -> Tree:
    """Parse a Java compilation unit, keeping the bytes behind node offsets."""
    return Tree(parser().parse(src).root_node, src)


def text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def line1(node, src: bytes, edge="start") -> int:
    """Compatibility helper; repeated lookups should use ``tree.line``."""
    offset = node.start_byte if edge == "start" else node.end_byte
    return LineIndex(src).line(offset)


def walk(node):
    """Every node in preorder, without recursion limits on deep source trees."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def methods(tree: Tree) -> list[dict]:
    """Method spans (annotations included), body spans, and nesting depth."""
    if tree._methods is None:
        out = []
        stack = [(tree.root, 0)]
        while stack:
            node, depth = stack.pop()
            if node is not tree.root and node.type in METHOD_TYPES:
                name = node.child_by_field_name("name")
                params = node.child_by_field_name("parameters")
                body = node.child_by_field_name("body")
                ptext = text(params, tree.src) if params else "()"
                method = {
                    "type": node.type,
                    "name": text(name, tree.src) if name else None,
                    "n_params": 0 if ptext == "()" else ptext.count(",") + 1,
                    "depth": depth,
                    "start": tree.line(node),
                    "end": tree.line(node, "end"),
                    "body_start": tree.line(body) if body else None,
                    "body_end": tree.line(body, "end") if body else None,
                    "abstract": body is None,
                }
                out.append(method)
                # Preserve the first preorder match when declarations share a line.
                tree._method_nodes.setdefault((method["start"], method["end"]), node)
            next_depth = depth + (node.type in NEST_TYPES) if node is not tree.root else depth
            stack.extend((child, next_depth) for child in reversed(node.children))
        tree._methods = out
    # Callers historically received mutable, independent dictionaries.
    return [dict(method) for method in tree._methods]


def enclosing(tree: Tree, line: int, methods_cache=None):
    """Innermost declaration containing a 1-based source line."""
    candidates = [m for m in (methods_cache if methods_cache is not None else methods(tree))
                  if m["start"] <= line <= m["end"]]
    return max(candidates, key=lambda m: (m["depth"], -(m["end"] - m["start"]))) \
        if candidates else None


def locate_method(tree: Tree, line: int, slack=8):
    declarations = methods(tree)
    method = enclosing(tree, line, declarations)
    if method is None:
        nearby = [m for m in declarations if abs(m["start"] - line) <= slack]
        method = min(nearby, key=lambda m: abs(m["start"] - line)) if nearby else None
    return method


def _comment(tree, node, include_text=True):
    body = text(node, tree.src)
    result = {"start": tree.line(node), "end": tree.line(node, "end"),
              "kind": "line" if node.type == "line_comment" else "block",
              "doc": body.startswith("/**")}
    if include_text:
        result["text"] = body
    return result


def comments(tree: Tree) -> list[dict]:
    """Comment nodes, computed once per immutable parsed source."""
    if tree._comments is None:
        tree._comments = [_comment(tree, node) for node in tree.nodes()
                          if node.type in COMMENT_TYPES]
    return [dict(comment) for comment in tree._comments]


def comment_lines(tree: Tree) -> list[bool]:
    """1-based mask, with the same padding as the original API."""
    size = len(tree.src.decode("utf-8", "replace").splitlines()) + 2
    mask = [False] * size
    for comment in comments(tree):
        for line in range(comment["start"], min(comment["end"] + 1, size)):
            mask[line] = True
    return mask


def doc_block(tree: Tree, method: dict) -> list[dict]:
    """Consecutive comment siblings immediately above a declaration."""
    if tree._methods is None:
        methods(tree)
    target = tree._method_nodes.get((method["start"], method["end"]))
    if target is None or target.parent is None:
        return []
    siblings = target.parent.children
    index = siblings.index(target)
    result, position = [], target.start_byte
    for node in reversed(siblings[:index]):
        if node.type not in COMMENT_TYPES or tree.src[node.end_byte:position].strip():
            break
        result.append(_comment(tree, node, include_text=False))
        position = node.start_byte
    return list(reversed(result))


def errors(tree: Tree) -> list[dict]:
    return [{"line": tree.line(node), "type": node.type,
             "text": text(node, tree.src)[:80]} for node in tree.nodes()
            if node.type == "ERROR" or node.is_missing]
