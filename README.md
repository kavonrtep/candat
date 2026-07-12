# candat

A terminal text editor with emacs keybindings, built on [Textual](https://textual.textualize.io/).

## Demo

![candat — a terminal editor with emacs keybindings](https://raw.githubusercontent.com/kavonrtep/candat/main/docs/brag.gif)

More than the basics, without leaving the terminal — emacs keys, split windows
on the same file, live markdown preview, a CSV table viewer, a large-file
pager, and a real shell inside.

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

Requires Python >= 3.10, on Linux or macOS.

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

Text files over 10 MB open in a `less`-style pager instead of the editor:
the file stays on disk behind a sparse line index (multi-GB files open with
bounded memory, with progress shown while indexing), and searches scan in
the background so the UI never freezes — `C-g` cancels one mid-flight.
Scroll (keys or mouse wheel), `g`/`G`, `/`/`?` and `C-s`/`C-r` search with
all visible matches highlighted, `C-x w` wrap, `M-g` goto line. `F` follows
a growing file like `less +F` (rotation-aware; any key stops), and `e`
loads the file into a real editor buffer if you insist. Binary files are
shown as a placeholder, and the truncated/binary views refuse to save, so a
partial view can never overwrite the real file.

`.csv` and `.tsv` files open in a table viewer (inspired by
[csvlens](https://github.com/YS-L/csvlens)): a sticky header, row cursor,
and original file line numbers in the gutter. Large files stream in as you
scroll rather than loading whole. In the table: `/` (or `C-s`) searches —
literal with smart case, the same dialect as everywhere else — with the
matched text highlighted in the cells (`Esc` clears), `n` repeats, `&`
filters rows by regex, `g`/`G` jump to top/bottom. `C-c C-v` switches
between the table and the raw text. The table is read-only.

The file-tree icons are emoji by default; if your terminal renders them poorly
(Konsole, some others), set `CANDAT_TREE_ICONS=nerd` (needs a Nerd Font) or
`=ascii`, or switch live with `M-x cycle-tree-icons` — the choice is saved.

## Configuration

`~/.config/candat/config.toml` (XDG aware), all keys optional:

```toml
tree_icons = "emoji"     # or "nerd" / "ascii"; cycle-tree-icons saves here
pager_wrap = false       # start the large-file pager with soft wrap on
tabstop = 8              # tab width in the pager
restore_session = true   # reopen last session's files (see below)
```

Starting candat without file arguments reopens the files you had open the
last time you quit in that directory — tabs, cursor positions, scroll, and
the active buffer (per project root, kept in `~/.local/state/candat/`).
Passing files on the command line skips the restore.

Files are read and written in whatever encoding and line ending they arrive
in: UTF-8 by default, a UTF-8/UTF-16 byte-order mark is honoured, and anything
else falls back to latin-1 so the bytes round-trip untouched (the status bar
shows the encoding and `CRLF`/`CR` when they aren't plain UTF-8/LF). Saves are
atomic — written to a temp file and renamed over the original — so a crash or
full disk never leaves a half-written file.

Unsaved edits are snapshotted to `~/.cache/candat/recovery/` every 20 seconds
and on a crash; a clean quit clears them, and if any survive they are reported
(never auto-applied) on the next launch. Crash logs (including hard faults
caught by `faulthandler`) land in `~/.cache/candat/`.

The file tree has a filter box on top: press `/` while the tree is focused (or click it), type to narrow the tree to files whose path matches, `Esc` clears it. The file tree opens files on selection. The default theme is `candat-light`
(high-contrast dark-on-white). The markdown preview is linked: it follows
the editor's scroll position.

## Stability

The keybindings are fixed emacs bindings by design — there is no rebinding
layer, and that is a deliberate choice, not a missing feature. `candat` is a
comfortable emacs-muscle-memory editor, not a configurable one.

From 1.0 onward the project follows semantic versioning: the config-file keys,
the `CANDAT_TREE_ICONS` variable, the command-line interface, and the on-disk
locations of the config, session, and recovery files are treated as stable and
will not change incompatibly within a major version. The keybindings and the
`M-x` command names are equally stable. Behaviour details not listed here
(exact status-bar wording, colours, internal module layout) may still change.

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
