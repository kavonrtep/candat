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
| `C-g` / `Esc` | cancel pending chord / prompt |
| `Ctrl+Shift+P` | command palette |

The file tree opens files on selection. The default theme is `pike-light`
(high-contrast dark-on-white).

## Development

```sh
uv run pytest
```

## Roadmap

1. ~~Skeleton: tree / tabs / status bar / C-x chords / open & save~~
2. Emacs editing: kill ring, C-s/C-r isearch, mark & region, M-x palette
3. Markdown mode: side-by-side live preview
4. Terminal panel (full PTY)
5. Polish
