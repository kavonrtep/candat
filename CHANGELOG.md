# Changelog

All notable changes to candat. The format is based on
[Keep a Changelog](https://keepachangelog.com/); from 1.0.0 candat follows
semantic versioning — see the Stability section of the README for what is
covered by the compatibility promise.

## [Unreleased]

## [1.0.0] - 2026-07-12

The 1.0 milestone is about trust, not features: saves can no longer corrupt
or truncate a file, unsaved work survives a crash, tables have no row limit,
and Linux + macOS are both tested in CI. From here on, the config keys, CLI,
keybindings, `M-x` command names, and on-disk file locations are stable
within a major version.

### Added
- **Crash recovery.** Buffers with unsaved edits are snapshotted to
  `~/.cache/candat/recovery/` every 20 seconds and on a crash, so a hard crash,
  `SIGKILL`, or power loss leaves a recent copy of your work. A clean quit
  clears them; any that survive are reported (never auto-applied) on the next
  launch.
- **Go to line in the editor (`M-g`)** — prompts for a line number and moves
  the cursor there (the pager already had its own `M-g`).
- **Table view for any buffer.** `C-c C-v` is now a universal "alternate view"
  key: markdown still cycles its preview; every other buffer — a `.tab`,
  `.gff`, a log, even an unsaved one — toggles into the delimited table view.
  The delimiter is auto-detected; `d` inside the table re-picks it (`,` `;`
  `|` `tab` `space` or any character) and re-parses in place. Clean file-backed
  buffers stream from disk as before; modified or untitled buffers are parsed
  from the buffer text, so the table always matches what you see. A
  `table_suffixes` config key extends which files open as tables
  automatically, and `M-x table-view` forces the table even on markdown.
  It works from the large-file pager too: both views stream, so `C-c C-v`
  flips a huge delimited file between pager and table (leaving the table on
  a large file returns to the pager, never a gigabyte editor load).
- **No more 200k-row limit.** Large delimited files (by size or estimated
  rows) open in a windowed table modelled on csvlens: a sparse row index is
  built by a background worker while the table renders and scrolls
  immediately, only the visible rows are ever read (row 3,000,000 fetches in
  under a millisecond), and search scans the whole file once on a worker,
  collecting every match — `n`/`N` then jump match-to-match instantly with a
  `match k/M` counter. Same keys as the classic table (`/`, `C-s`/`C-r`,
  `d`, `g`/`G`), including the `&` row filter: one background scan collects
  the matching rows (8 bytes each, so millions are fine), the view pages
  through them with original line numbers in the gutter, and search results
  intersect with the filter so `n`/`N` only visit visible rows. Windowed
  tables also join the disk-change watch: a growing file extends the table
  in place; rotation/truncation reloads it. Files with quoted multi-line
  fields stay on the classic (exact) parser.
- **macOS support.** The code was already POSIX-generic; CI now tests on
  macOS as well as Linux across Python 3.10 and 3.13.

### Changed
- **Saves are now safe.** Files are written atomically (temp file + rename), so
  a crash or full disk can no longer truncate a file mid-write.
- **Encoding and line endings round-trip.** A file's encoding (UTF-8, a UTF-8
  or UTF-16 BOM, or a latin-1 fallback that preserves any byte sequence) and
  its line ending (LF/CRLF/CR) are detected on load and restored on save — a
  non-UTF-8 or CRLF file is no longer silently corrupted or converted. The
  status bar shows the encoding and `CRLF`/`CR` when they aren't plain UTF-8/LF.

### Fixed
- **UTF-16 files no longer misdetected as binary** (their NUL bytes are text
  when a byte-order mark is present).
- **`C-r` steps to the previous match in the CSV/TSV table** (with `N` as an
  alias). Backward search worked in the editor and pager but the table only
  went forward — `C-r` fell through to the editor's isearch and did nothing.

## [0.12.0] - 2026-07-12

### Added
- **Session restore.** Starting `candat` without file arguments reopens the
  files you had open the last time you quit in that directory — tab order,
  cursor positions, scroll, and the active buffer, kept per project root in
  `~/.local/state/candat/sessions.json` (the 50 most recent roots). Passing
  files on the command line skips the restore; `restore_session = false` in
  the config turns it off.

### Changed
- **One search dialect everywhere.** The CSV table's `/` and `C-s` search is
  now literal with smart case, matching the editor's isearch and the pager,
  instead of regex. (The `&` row filter is still regex, as labelled.)

## [0.11.0] - 2026-07-12

### Added
- **A config file: `~/.config/candat/config.toml`** (XDG aware). Keys:
  `tree_icons` (emoji / nerd / ascii — `cycle-tree-icons` now persists your
  choice), `pager_wrap` (start the pager wrapped) and `tabstop` (tab width in
  the pager). `$CANDAT_TREE_ICONS` still overrides.
- **Mouse-wheel scrolling in the pager** (scrolling up also unpins follow
  mode, like any key).
- **Indexing progress.** Opening a multi-GB file in the pager shows
  `indexing… 34% (2,150 / 6,300 MB)` in the view and status bar instead of a
  bare `indexing…`.
- **Hard-crash tracebacks.** `faulthandler` writes SIGSEGV/SIGBUS/SIGABRT
  tracebacks (e.g. a native tree-sitter fault) to `~/.cache/candat/`, where
  the Python-level crash log can't reach; the file is dropped on a clean exit.
- **Follow mode in the pager (`F`).** Like `less +F`: sticks to the end of a
  growing file, indexing only the new tail; detects rotation/truncation and
  reopens. Any key (or `C-g`) stops following.
- **Open-in-editor escape hatch from the pager (`e` / `v`).** Loads the whole
  file into a normal, editable buffer after a confirmation (syntax
  highlighting stays off above the large-file threshold).

### Changed
- **Pager search no longer blocks the UI.** The scan runs chunk-by-chunk on a
  worker thread, shows `searching…` in the status bar, and `C-g` / `Esc`
  cancels it mid-flight. Backward repeat no longer rescans from the start of
  the file on every keypress, and wrap-around no longer re-searches the region
  already covered.
- **The pager reads with `pread` instead of mmap.** A file truncated or
  rotated underneath the pager (e.g. logrotate) now yields blank lines instead
  of killing the process with SIGBUS.
- **Long single lines are cheap.** A line is read at most 64 KB deep for
  display (marked with `…`), so a gigabyte-long minified-JSON line no longer
  costs a full decode and wrap pass per keystroke.
- **Cell-accurate rendering in the pager.** Wrapping, cropping and horizontal
  scrolling count terminal cells, so tabs (expanded to 8), CJK and emoji line
  up; the `›` truncation chevron lands on the right column.
- **Unicode smart case in the pager search.** A lowercase query now matches
  uppercase content beyond ASCII (e.g. `čau` finds `ČAU`).
- **A failed pager search resets the query**, so the next `C-s` prompts for a
  fresh term instead of repeating the miss.
- **CSV search highlighting is lighter.** Cells stay plain strings unless they
  match the active search; starting/cancelling a search rewrites only the
  matching cells (bounded at 50,000 rows) instead of every loaded cell.
- **Large/binary read-only guard now lifts** when a reload brings the buffer
  back to a normal, fully loaded state, instead of leaving it stuck read-only.
- **Pager panes join the disk-change watch.** A growing file extends the index
  in place (your viewport stays put); a rotated or truncated file reopens —
  previously the pager went silently stale unless you pressed `F`.

### Fixed
- **Re-pointing the pager at a new file no longer races the indexer** (the old
  index build could hit a closed file handle); stale opens and searches are
  discarded by generation.
- **Crash logs survive Textual API changes**: the traceback is also recovered
  in `main()` after the app exits, and Textual is pinned `<9` since the icon
  cycling, isearch highlight and crash hook lean on internals.

## [0.10.0] - 2026-07-12

### Added
- **Match highlighting in the large-file pager.** Every occurrence of the
  search term in the visible area is highlighted, not just the current one.
  `C-g` / `Esc` cancels the search and clears the highlight, so `C-s` prompts
  for a new term instead of staying locked on the old one.
- **All-match highlighting in the editor's incremental search.** While `C-s` /
  `C-r` is active, every occurrence in the visible area is highlighted (the
  current match still rides the selection); the highlight clears when the
  search ends.
- **Match highlighting in the CSV/TSV table.** Search (`/`) highlights the
  matched text inside the cells, not just the row cursor; new rows are
  highlighted as they stream in, and `C-g` / `Esc` clears it in place without
  losing your scroll position.

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
