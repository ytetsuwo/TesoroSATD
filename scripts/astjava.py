"""Java AST compatibility API.

The implementation lives in java_tree; existing numbered pipeline stages keep
their imports unchanged. Line indexing and cached traversals belong to a Tree,
while mutable parsers are private to each worker thread.
"""
from java_tree import (
    COMMENT_TYPES, LANGUAGE, METHOD_TYPES, NEST_TYPES, Tree,
    comment_lines, comments, doc_block, enclosing, errors, line1,
    locate_method, methods, parse, parser, text, walk,
)
