# Changelog

All notable changes to candat. The format is based on
[Keep a Changelog](https://keepachangelog.com/); candat follows semantic
versioning loosely (it is pre-1.0, so minor versions may include behaviour
changes).

## [Unreleased]

### Added
- **Match highlighting in the large-file pager.** Every occurrence of the
  search term in the visible area is highlighted, not just the current one.

### Changed
- **Consistent search-repeat with `C-s`.** In the pager and the CSV/TSV table,
  `C-s` now steps to the *next* match (and `C-r` to the previous, in the pager)
  when a search is active — matching the editor's isearch — instead of
  re-prompting. `/` (and `?` in the pager) still start a fresh search. Pager
  next/previous advances past the current match, so repeated hits on one line
  are each reachable.

## [0.9.0] - 2026-07-12

### Changed
- **Large text files now open in a `less`-style pager** instead of the
  read-only first-10,000-lines view. The file is memory-mapped with a sparse
  line index (built in a background thread), so multi-GB files open instantly
  with bounded memory and full navigation: scroll / page / `g` / `G`, wrap
  toggle (`C-x w`, off by default — long lines truncate with a `›` marker and
  scroll horizontally), streaming search (`C-s` / `C-r`, smart case, wraps)
  with `n` / `N` to repeat, and go-to-line (`M-g`). Binary files still open
  as a read-only placeholder.

### Fixed
- Cycling file-tree icons (`cycle-tree-icons`) now updates the glyphs
  immediately instead of only when the tree regains focus.

## [0.8.0] - 2026-07-10

### Added
- **Large-file guard.** Files over 10 MB open in a read-only view showing the
  first 10,000 lines, so a huge log never freezes the editor or exhausts
  memory. The status bar marks it `large: head only`.
- **Binary-file detection.** Files with NUL bytes open as a read-only
  placeholder instead of garbage.
- **Crash handler.** An unhandled exception writes a full traceback to
  `~/.cache/candat/crash-<timestamp>.log` and prints its path on exit (Textual
  still restores the terminal).
- **Selectable file-tree icons** for terminals that render emoji poorly (e.g.
  Konsole): set `CANDAT_TREE_ICONS=emoji|nerd|ascii`, or switch live with the
  `cycle-tree-icons` command (`M-x`).
- This changelog.

### Changed
- Saving a large or binary view is disabled, so a partial/placeholder buffer can
  never overwrite the real file.

## [0.7.1] - 2026-07-10

### Fixed
- The find-file / write-file prompt no longer selects the whole prefilled path,
  so `Backspace` deletes a single character (walking up a directory level)
  instead of wiping the path.

## [0.7.0] - 2026-07-09

### Added
- **Window splitting.** `C-x 3` (side by side) and `C-x 2` (stacked) open the
  current buffer in a new window as a linked view — same file and edits, but an
  independent cursor and scroll, for inspecting two places at once. `C-x 0`
  closes a window, `C-x 1` keeps only the current one, `C-x o` cycles windows.
- **Find-file completion choices.** `Tab` lists the matching paths in a
  navigable panel when more than one matches.

## [0.6.0] - 2026-07-05

### Added
- Syntax highlighting for INI/config formats, Makefiles, and Dockerfiles, with
  language detection by filename (Makefile, Dockerfile, setup.cfg, `.bashrc`, …).
- Optional soft wrap per buffer (`C-x w`).

## [0.5.0] - 2026-07-04

### Added
- A filter box on the file tree: press `/` in the tree to narrow it to files
  whose path matches; `Esc` clears.

## [0.4.1] - 2026-07-03

### Changed
- Packaging: MIT license, PyPI metadata, Python 3.10 floor.

## [0.4.0] - 2026-07-03

### Added
- **CSV/TSV table viewer.** `.csv`/`.tsv` files open in a streaming table
  (sticky header, row cursor, file-line numbers); `/` search, `n` next,
  `&` regex row filter, `g`/`G` jump. Large files stream in as you scroll.

## [0.3.0] - 2026-07-03

### Added
- Read-only mode (`C-x C-q`) and open-read-only (`C-x C-r`).
- Auto-reload of files changed on disk: clean buffers reload in place; buffers
  with local edits ask before discarding them.

## [0.2.2] - 2026-07-03

### Added
- Send-to-REPL (`C-c C-c`), project-wide search (`C-x g`), query-replace
  (`M-%`), and comment toggle (`M-;`).
- Terminal scrollback (`Shift+PgUp`/`PgDn`).

### Fixed
- Terminal spawn/timing races (input discarded before the shell was ready; the
  pty forked at the wrong size while the panel was hidden).

## [0.1.1] - 2026-07-02

First public release on PyPI. The initial build already included:

- File tree, tabbed editor buffers, the `C-x` chord dispatcher, a status bar,
  and the high-contrast `candat-light` theme.
- Emacs editing layer: kill ring, mark/region, `C-s`/`C-r` incremental search,
  the `M-x` command palette, emacs movement keys, and `M-up`/`M-down` line
  moving.
- Side-by-side live markdown preview (`C-c C-v`), scroll-linked.
- Full PTY terminal panel (`C-x t`) running a real shell.
