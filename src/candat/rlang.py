"""R syntax highlighting: grammar from tree-sitter-language-pack plus a
hand-written highlight query (the pack ships no queries)."""

from __future__ import annotations

R_HIGHLIGHTS = """\
(comment) @comment
(string) @string
(escape_sequence) @string.escape
(integer) @number
(float) @number
(complex) @number
[(true) (false)] @boolean
[(null) (na) (inf) (nan)] @constant
(dots) @variable.special

[ "if" "else" "for" "while" "repeat" "function" "in" ] @keyword
[ (break) (next) ] @keyword

[ "<-" "<<-" "->" "->>" "=" ] @operator
(binary_operator operator: _ @operator)
(unary_operator operator: _ @operator)
(extract_operator operator: _ @operator)
(namespace_operator operator: _ @operator)

(call function: (identifier) @function.call)
(binary_operator
  lhs: (identifier) @function
  operator: "<-"
  rhs: (function_definition))
(parameter name: (identifier) @variable.parameter)

[ "(" ")" "{" "}" "[" "]" "[[" "]]" ] @punctuation.bracket
(comma) @punctuation.delimiter
"""


def r_language():
    """The compiled R grammar, or None if the optional dep is missing."""
    try:
        from tree_sitter_language_pack import get_language
    except ImportError:
        return None
    return get_language("r")
