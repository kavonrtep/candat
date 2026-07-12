# candat

A terminal IDE with emacs keybindings, built on [Textual](https://textual.textualize.io/).

## Demo

![candat — a full IDE in your terminal](https://raw.githubusercontent.com/kavonrtep/candat/main/docs/brag.gif)

A full IDE in the terminal — split windows on the same file, live markdown
preview, a CSV viewer, and a real shell inside.

## Install

From [PyPI](https://pypi.org/project/candat/), as a standalone tool
(recommended — gives you a global `candat` command in its own isolated
environment):

```sh
uv tool install candat
# or, with pipx (needs Python >= 3.10):
pipx install candat
```

Or with plain pip into the environment of your choice: `pip install candat`.
To try it once without installing anything permanently: `uvx candat`.

The development version installs straight from GitHub:

```sh
uv tool install git+https://github.com/kavonrtep/candat
```

Requires Python >= 3.10 and Linux.

## Run

```sh
candat [FILE|DIR ...]
```

Passing a directory sets the file-tree root; files are opened in buffers.

## Keys (so far)

| Key | Action |
| --- | --- |
| `C-x C-f` | find file (opens new file if it doesn't exist) |
| `C-x C-s` | save buffer |
| `C-x C-w` | write buffer to another file |
| `C-x C-q` | toggle read-only (status bar shows `%%`) |
| `C-x w` | toggle soft wrap for this buffer |
| `C-x C-r` | open a file read-only |
| `C-x k` | kill buffer |
| `C-x b` | buffer list (Enter switches; next buffer preselected) |
| `C-x o` | move focus: tree → window(s) → terminal |
| `C-x 3` / `C-x 2` | split window side-by-side / stacked (same buffer, linked view) |
| `C-x 0` / `C-x 1` | close this window / the others |
| `C-x C-c` | quit (confirms if unsaved buffers) |
| `C-x C-x` | exchange point and mark |
| `C-x h` | mark whole buffer |
| `C-x u`, `C-/`, `C-z` | undo |
| `C-f` `C-b` `C-n` `C-p` `C-a` `C-e` | char/line movement |
| `M-f` `M-b` | word movement |
| `C-v` `M-v` | page down / up |
| `M-<` `M->` | beginning / end of buffer |
| `C-space` | set mark (movement extends region) |
| `C-k` | kill line (consecutive kills accumulate) |
| `C-w` / `M-w` | kill / copy region |
| `C-y` / `M-y` | yank / yank-pop |
| `M-d` / `M-backspace` | kill word forward / backward |
| `M-up` / `M-down` | move current line (or marked block) up / down |
| `C-s` / `C-r` | incremental search (smart case, wraps) |
| `M-%` | query-replace (y/n/!/q) |
| `C-x g` | project-wide regex search (results list, Enter jumps) |
| `M-;` | toggle line comment |
| `C-c C-c` | send region or current line to the terminal REPL |
| `C-x t` | toggle terminal panel (keys pass through raw; only `C-x` is reserved) |
| `Shift+PgUp/PgDn` | terminal scrollback (typing snaps back) |
| `C-c C-v` | cycle markdown preview: split / preview-only / off |
| `M-x`, `Ctrl+Shift+P` | command palette |
| `C-g` / `Esc` | cancel chord / prompt / search / mark |

`ESC` acts as the Meta prefix, so `ESC w` == `M-w`, `ESC x` == `M-x`, etc.
`Tab` completes paths in the find-file and write-file prompts, listing the
choices (navigable with arrows, Enter to pick) when more than one matches.

Open files are watched for external changes: clean buffers reload
automatically; buffers with local edits ask before discarding them.

Very large text files open in a `less`-style pager (scroll, `g`/`G`, `C-s`/`C-r` search, `C-x w` wrap); binary files are shown as a placeholder.

`.csv` and `.tsv` files open in a table viewer (inspired by
[csvlens](https://github.com/YS-L/csvlens)): a sticky header, row cursor,
and original file line numbers in the gutter. Large files stream in as you
scroll rather than loading whole. In the table: `/` (or `C-s`) searches,
`n` repeats, `&` filters rows by regex, `g`/`G` jump to top/bottom. `C-c
C-v` switches between the table and the raw text. The table is read-only.

The file-tree icons are emoji by default; if your terminal renders them poorly
(Konsole, some others), set `CANDAT_TREE_ICONS=nerd` (needs a Nerd Font) or
`=ascii`, or switch live with `M-x cycle-tree-icons`.

The file tree has a filter box on top: press `/` while the tree is focused (or click it), type to narrow the tree to files whose path matches, `Esc` clears it. The file tree opens files on selection. The default theme is `candat-light`
(high-contrast dark-on-white). The markdown preview is linked: it follows
the editor's scroll position.

## Development

```sh
uv run pytest
```

Release history is in [CHANGELOG.md](CHANGELOG.md).

## Roadmap

1. ~~Skeleton: tree / tabs / status bar / C-x chords / open & save~~
2. ~~Emacs editing: kill ring, C-s/C-r isearch, mark & region, M-x palette~~
3. ~~Markdown mode: side-by-side live preview (debounced)~~
4. ~~Terminal panel (full PTY: forkpty + pyte)~~
5. ~~Polish: terminal scrollback, dirty-line rendering, path completion in
   prompts, buffer list, scroll-synced preview, R/xml/html highlighting~~

Syntax highlighting covers python, markdown, json, yaml, bash, html, xml,
css, toml, js, sql, go, rust, java, R, and config formats — INI/`.cfg`/
`.conf`, Makefiles, Dockerfiles, and shell dotfiles (`.bashrc`, `.env`, …).
