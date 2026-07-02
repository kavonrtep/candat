# pike

A terminal IDE with emacs keybindings, built on [Textual](https://textual.textualize.io/).

## Run

```sh
uv run pike [FILE|DIR ...]
```

Passing a directory sets the file-tree root; files are opened in buffers.

## Keys (so far)

| Key | Action |
| --- | --- |
| `C-x C-f` | find file (opens new file if it doesn't exist) |
| `C-x C-s` | save buffer |
| `C-x C-w` | write buffer to another file |
| `C-x k` | kill buffer |
| `C-x b` | next buffer |
| `C-x o` | switch focus between tree and editor |
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
| `C-s` / `C-r` | incremental search (smart case, wraps) |
| `C-x t` | toggle terminal panel (keys pass through raw; only `C-x` is reserved) |
| `C-c C-v` | cycle markdown preview: split / preview-only / off |
| `M-x`, `Ctrl+Shift+P` | command palette |
| `C-g` / `Esc` | cancel chord / prompt / search / mark |

The file tree opens files on selection. The default theme is `pike-light`
(high-contrast dark-on-white).

## Development

```sh
uv run pytest
```

## Roadmap

1. ~~Skeleton: tree / tabs / status bar / C-x chords / open & save~~
2. ~~Emacs editing: kill ring, C-s/C-r isearch, mark & region, M-x palette~~
3. ~~Markdown mode: side-by-side live preview (debounced)~~
4. ~~Terminal panel (full PTY: forkpty + pyte)~~
5. Polish: terminal scrollback, dirty-line rendering, path completion in
   prompts, buffer list, scroll-synced preview, R/xml/html highlighting
