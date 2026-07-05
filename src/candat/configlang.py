"""Syntax highlighting for config formats and Makefiles.

The grammars come from tree-sitter-language-pack, which ships parsers but no
highlight queries, so the queries below are hand-written (as for R). Each is
registered on demand from the editor.
"""

from __future__ import annotations

INI_HIGHLIGHTS = """\
(comment) @comment
(section_name) @function
(setting_name) @variable
(setting_value) @string
"=" @operator
["[" "]"] @punctuation.bracket
"""

MAKE_HIGHLIGHTS = """\
(comment) @comment
(variable_assignment (word) @variable)
(targets (word) @function)
(prerequisites (word) @string)
(variable_reference (word) @variable)
(include_directive "include" @keyword)
["=" ":"] @operator
["$" "(" ")"] @punctuation.bracket
"""

DOCKERFILE_HIGHLIGHTS = """\
(comment) @comment
[
  "FROM" "AS" "RUN" "CMD" "ENV" "COPY" "ADD" "WORKDIR" "EXPOSE"
  "ENTRYPOINT" "VOLUME" "USER" "ARG" "LABEL" "ONBUILD" "STOPSIGNAL"
  "HEALTHCHECK" "SHELL" "MAINTAINER"
] @keyword
(image_name) @type
(image_tag) @number
(image_alias) @variable
(path) @string
(json_string) @string
"""

# language name -> (pack grammar name, highlight query)
CONFIG_LANGUAGES = {
    "ini": ("ini", INI_HIGHLIGHTS),
    "make": ("make", MAKE_HIGHLIGHTS),
    "dockerfile": ("dockerfile", DOCKERFILE_HIGHLIGHTS),
}


def config_grammar(name: str):
    """The compiled grammar for a config language, or None if unavailable."""
    spec = CONFIG_LANGUAGES.get(name)
    if spec is None:
        return None
    try:
        from tree_sitter_language_pack import get_language
    except ImportError:
        return None
    return get_language(spec[0])
