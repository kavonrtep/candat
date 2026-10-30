# Performance and responsiveness review

Date: 2026-10-30  
Project: candat 1.7.0  
Baseline: commit `4aa9393`, including the existing working-tree changes  
Scope: review only; no application or test code changes

## Assessment

The most actionable problem is **unbounded or relatively large synchronous work on Textual's UI event loop**. The project already has useful protections: a large-file pager, sparse indexes, background initial indexing/search, preview debouncing, stale-result checks, and a syntax-highlighting size threshold. However, several adjacent paths bypass those protections. A spinner painted before a blocking operation does not make that operation responsive.

The strongest candidates for occasional freezes are:

1. Terminal paste/send operations writing to a blocking PTY.
2. CSV end navigation, searches, filtering, and table population.
3. Periodic synchronous indexing of appended file data.
4. Replace-all repeatedly editing and rescanning a document without yielding.
5. Synchronous recovery writes, save/fsync, and reloads.

Typing latency has a separate cause: whole-document work on edits, especially syntax highlighting and markdown processing, multiplied across linked views. Fix the hard stalls first, then profile and reduce edit/render work.

This review establishes code paths and includes small local measurements. It does **not** establish which path caused the freezes in your actual sessions: no freeze trace or representative user workload was supplied.

## Method and evidence

Reviewed application loading, editing, search/replace, tables, pager, terminal, preview, navigation, persistence, and related tests. Checked the installed Textual implementation rather than assuming that a widget method is cheap. Local environment: Python 3.13.2, Textual 8.2.8.

The repository already had modifications to `src/candat/csvview.py` and `tests/test_csvview.py`, and untracked `test` and `wide.csv`. They were left untouched. Findings refer to the working files as reviewed; line numbers may move with subsequent edits.

A temporary probe at `/tmp/candat_perf_review_probe.py` exercised existing methods without modifying source. CSV timings used a headless Textual app with a mounted `CsvViewer`, a three-column, 50,000-row in-memory CSV of 1,116,683 characters. Each operation was timed synchronously, with the event loop allowed to settle between operations. Index timings used an in-memory reader returning 1 MiB chunks, isolating indexing CPU from disk latency.

| Operation | Measured duration | Interpretation |
| --- | ---: | --- |
| CSV initial open, first 500 rows | 29.5 ms | Initial population is relatively small on this fixture |
| CSV load remaining rows to 50,000 | 953.1 ms | Almost a second in a single UI-thread method |
| CSV absent search after loading all rows | 205.7 ms | Restyle scan plus unsuccessful search blocks interaction |
| CSV absent filter over all 50,000 input rows | 67.0 ms | Even zero results require reading all source rows |
| Index 65 MiB of two-byte lines | 3,816.5 ms | Multi-second CPU stall without any physical disk reads |
| Index 65 MiB of 80-byte lines | 98.9 ms | Byte limits do not control execution time |
| Dirty-state check, 0.8 MB text | 0.103 ms average | Small individually in this setup |
| Dirty-state check, 8 MB text | 1.730 ms average | Linear overhead, but not itself a demonstrated multi-second freeze |

These are single-run diagnostic timings, except dirty checks averaged over 50 calls. They exclude subsequent paint/layout cost and are not end-to-end latency percentiles. The dirty-check fixture has a differently sized saved baseline, so comparison can short-circuit; it primarily measures document reconstruction, not a worst-case equal-length comparison. The 65 MiB scan corresponds to the current `> 64 MiB` stop condition checked between 1 MiB chunks.

No full test-suite run was performed: this is a source review with targeted measurements, not a code-change validation. No external documentation or production telemetry was used.

## Findings and recommendations

### 1. High: blocking terminal writes can hang the entire UI

**Evidence:** `src/candat/terminal.py:175` creates the PTY; `_feed` at line 208 flushes pending input with `os.write`; `on_key`, `on_paste` at line 336, and `send_text` at line 341 also write directly. The descriptor is not configured for nonblocking writes.

**Trigger:** Paste a large block or send a large region to a child process that is not consuming stdin. Once PTY buffering fills, a write may wait for the child. Because that write runs on the UI thread, navigation and cancellation cannot be processed. The output reader also waits for its UI callback, which can aggravate backpressure when the child is producing output.

**Recommendation:** Use a bounded output queue and an asynchronous/nonblocking write pump. Preserve byte order, track partial writes, handle `EAGAIN`, and provide cancellation and a visible pending-byte count. If changing descriptor flags, update the reader as well: its current `OSError` handling treats errors as terminal exit. A dedicated writer is another option, provided shutdown and cancellation can release blocked writes. Do not simply move unrestricted writes into unlimited workers.

**Validation:** Start a controlled child that does not read input, submit a multi-megabyte paste, and verify that editor interaction and cancel still work. Then resume input consumption and check exact byte delivery. Test shutdown with queued input.

**Confidence:** Blocking path confirmed by inspection; a live hung PTY was not deliberately reproduced.

### 2. High: classic CSV operations are synchronous and can scan/populate the entire dataset

**Evidence:** `src/candat/csvview.py`, `load_more`, `load_all`, `_restyle_loaded`, `search_next`, `search_prev`, and `apply_filter`. `G` calls `load_all`. `src/candat/app.py:635` routes files to this implementation unless size/estimated row count warrants `BigTable`; quoted multiline fields force the classic path. Modified and untitled buffers also use the classic viewer.

`load_more(count)` limits **accepted** rows, not source rows examined. A selective filter can therefore scan an arbitrarily large source before returning even once. An absent search repeatedly loads batches until exhaustion. The 50,000-row highlighting cap still allows a large synchronous cell scan; wide tables multiply the work. `MAX_ROWS=200_000` bounds stored rows, not the time spent filtering rejected rows.

**Observed:** A roughly 1.1 MB, 50,000-row fixture spent 953 ms loading the remaining rows and 206 ms searching an absent term after loading. This is below both the 10 MiB routing threshold and 200,000-row threshold.

**Recommendation:** Separate parsing/search/filtering from widget population. Use workers for source scanning; deliver bounded row batches and apply them on the UI thread with a small elapsed-time budget. Check cancellation against source work, even when no rows match. Highlight only visible cells where feasible. Adopt windowed viewing at a much lower measured cost threshold, including column count, and plan a record-aware index for multiline CSV. Do not force multiline CSV through the current physical-line index.

**Validation:** `G`, absent search, sparse filter, reload, and delimiter changes on narrow/wide files; include multiline records and unsaved buffers. Measure event-loop delay while jobs run, not just completion time.

### 3. High: periodic tail indexing can block for seconds

**Evidence:** `src/candat/pager.py:734`, `TextPager.check_disk`; `src/candat/bigtable.py:295`, `BigTable.check_disk`. Both call `LineIndex.scan` synchronously for growing files, with a 64 MiB byte budget. The app polls every second (`src/candat/app.py:300`); pager follow mode has another polling path.

The scan performs a Python iteration per newline. A fixed byte budget is therefore a poor latency budget. The current strict `>` condition permits 65 MiB for full-size chunks. The memory-only probe took 3.82 seconds for short lines. Disk latency would add to that cost. Several growing buffers can compound the delay in one polling pass.

**Recommendation:** Extend indexes in one background job per file, publishing stable snapshots or synchronized progress. Avoid concurrent mutation of an index by follow and general polling. Coalesce notifications and cancel obsolete jobs after rotation/reload. If an interim UI-thread implementation is retained, use both a very small wall-time budget and byte/line limits; blocking disk reads still need offloading.

**Validation:** Append bursts with 2-, 80-, and 4,096-byte lines; test several open logs, rotation, truncation, and follow mode. Verify bounded event-loop delay throughout catch-up.

### 4. High: replace-all compounds document work inside one uninterrupted handler

**Evidence:** `src/candat/replace.py:84`, `on_key`, handles `!` with a synchronous loop. Every match invokes an editor replacement, materializes the entire text through `_haystack`, potentially lowercases it, and converts offsets. `EditorBuffer.edit` replays edits to linked views.

For K matches in a document of size N, repeated whole-document construction/search preparation alone can approach O(K × N), in addition to highlighting, wrapping, undo bookkeeping, and queued change messages. Cancellation cannot run until the loop returns.

**Recommendation:** Find replacement spans once against a versioned snapshot, preserving current smart-case and replacement semantics. Compute the transformed range/text away from the UI where warranted. Apply a deliberate undoable transaction, or bounded batches if a single application is itself expensive. Check the source revision before commit. Avoid assigning `editor.text` as a shortcut: Textual's setter clears edit history. Preserve linked-view positions and define cancellation behavior explicitly.

**Validation:** Thousands of replacements with growing/shrinking replacement strings, multiple linked views, highlighted markdown, and a single-step undo expectation. Assert both final content and responsiveness.

### 5. High: disk operations remain on the event loop, including periodic recovery

**Evidence:** `src/candat/app.py:307–329` collects complete dirty-buffer strings and writes recovery snapshots every 20 seconds; `src/candat/recovery.py:33` writes text and metadata synchronously. `app.py:1023` stats open paths and can synchronously reload; `_load_text` at line 492 calls `editor.load` synchronously after painting a status message. `src/candat/editor.py:201` includes `fsync` in atomic writes, called by the synchronous save path.

**Trigger:** Dirty large buffers, slow disks, network-mounted files/home directories, external rewrites, or several simultaneous reloads. A pause near a 20-second cadence is a useful clue for recovery; a one-second cadence suggests watching/growth handling.

**Recommendation:** Keep atomic save guarantees, but perform file read/decode, write/fsync/rename, and metadata polling outside the UI thread. Use one ordered persistence job per logical buffer and immutable snapshots tagged with revision numbers. Mark a buffer clean only if its current revision still matches the saved revision. Recovery should skip unchanged revisions and avoid overlapping snapshot jobs. Serialize recovery cleanup with outstanding writes so a completed worker cannot recreate a snapshot after clean exit. Deduplicate linked files when polling.

Reading in a worker is only part of loading: installing a TextArea document, wrapping, and highlighting remain UI costs. Measure them separately, set language/size policy before installation, and offer pager fallback when installation is too expensive. A loading label alone does not solve this.

**Validation:** Inject delayed stat/read/write/fsync operations and verify continued interaction, correct save ordering, edits made during save, and reliable recovery/cleanup.

### 6. Medium–high: editing rebuilds document-wide highlighting, with extra markdown work

**Evidence:** `src/candat/editor.py:394`, `_build_highlight_map`, scans all markdown lines, discovers fences, builds code-row sets, and applies inline/block highlights. Installed Textual 8.2.8 calls this method on every edit (`widgets/_text_area.py:1676–1700`); its base implementation also rebuilds the highlight map. `codeblocks.py` caches block parsing, but block source strings and highlight output are still reconstructed. Changing a large block creates a new cache key and parse.

`EditorBuffer.edit` at line 470 repeats edits for each linked view. `_on_text_area_changed` at line 699 reconstructs full text for dirty checking: Textual's `Document.text` joins all lines. Markdown structural commands also copy all document lines (`_doc_lines`, line 1036). Initial-load highlighting limits do not constitute an ongoing budget as buffers grow; linked-view construction and converted markdown deserve the same guardrails.

**Recommendation:** Profile edits with preview disabled to isolate editor cost. Cache fence structure and invalidate changed regions, extending invalidation when an opener/closer changes. Cache per-line inline highlights; avoid rebuilding unaffected block source. Apply highlighting limits consistently on load, linked-view creation, converted documents, and growth. Use revision/save-point tracking or incremental content identity for dirty state while preserving the current behavior that returning exactly to saved content clears the modified flag. Consider a shared document/highlighting model across views only after smaller changes are measured.

**Validation:** Compare plain text and markdown, one/two/four views, long fenced blocks, many short lines, edits near document end, undo back to saved content, and files growing across thresholds. The measured 1.73 ms dirty check is a secondary cost, not evidence that it alone causes the reported freezes.

### 7. Medium–high: BigTable performs disk reads and substantial work during rendering

**Evidence:** `src/candat/bigtable.py:321–348`, `_read_lines`/`fetch_rows`, and `render` at line 394. Unfiltered rendering fetches a viewport; filtered rendering calls `fetch_rows(..., 1)` for each scattered row. `_read_lines` reads in **1 MiB** chunks even for one row, decodes/splits the entire fetched data, then slices. The separate 64 KiB seek chunk does not reduce this body-read size.

A filtered viewport of 40 scattered rows can request roughly 40 MiB of body data per render, plus seeks, even when each row is tiny. Warm filesystem caches may hide disk cost but not copying/decoding. There is no persistent parsed-row viewport cache in this path. Column width work and row construction also scale with columns outside the visible horizontal area.

**Recommendation:** Use bounded record reads, cache parsed row/page data by file generation and delimiter, coalesce adjacent filtered rows, prefetch asynchronously, and render from ready cache entries. Bound cached bytes, not just row count. Limit formatting to visible columns plus a small overscan. Preserve the last viewport while new data arrives rather than blocking paint.

**Validation:** Compare filtered/unfiltered scrolling, warm/cold cache, slow storage, and hundreds of columns. Count requested bytes and decoded bytes per frame; a small visible viewport should not routinely require tens of MiB.

### 8. Medium–high: background regex filtering is not reliably cancellable

**Evidence:** `src/candat/bigtable.py:704`, `_filter_worker`, executes Python `re.search` for each complete raw line and checks cancellation only between chunks. The classic viewer executes regex filters directly on the UI thread.

A pathological backtracking pattern can spend a long time inside one match. Moving Python's regular-expression engine to a thread does not provide a reliable CPU/GIL isolation boundary or a way to interrupt that individual match. Long newline-free records also cause repeated `carry + chunk` copying in the BigTable filter, growing memory with record length.

**Recommendation:** Use a regex engine with enforceable per-match limits or a separately terminable process for user regex jobs; document any syntax changes. Limit and handle long records explicitly without silently changing search results. Cancellation checks need to bound actual work, not just chunk transitions.

**Validation:** Run adversarial patterns and long records only in a supervised subprocess with a hard timeout. Confirm the main UI stays responsive and that cancellation actually stops resource use. This risk was identified statically; no unbounded regex was run in the application.

### 9. Medium: worker completion can reintroduce a large UI-thread stall

**Evidence:** `src/candat/bigtable.py:627`, `_recompute_matches`, intersects search and filter arrays with a Python two-pointer loop. It is called from completion/filter paths on the UI thread. With millions of matching/filtered rows, completing a background search can still produce a noticeable pause.

Match/filter arrays use eight bytes per row identifier. Ten million entries require about 80 MB per array, excluding other buffers; a separate intersection can add another large allocation. The no-filter case correctly aliases the original match array.

**Recommendation:** Compute intersections in the same cancellable background pipeline and publish only a current-generation result. Budget aggregate result memory; consider bitmaps, compressed sets, or disk-backed results for dense matches, based on measured workloads. Retain fast bisect-based next/previous navigation.

**Validation:** Dense search plus dense filter on multi-million-row files, cancel while intersecting, and repeated result replacement. Measure peak RSS as well as completion callback time.

### 10. Medium: terminal output parsing and shutdown still occupy the UI thread

**Evidence:** `src/candat/terminal.py:195–228` reads up to 65,536 bytes in a worker but feeds pyte on the UI thread and refreshes on each callback. `kill` at line 249 can sleep in a 50 × 10 ms loop and subsequently call blocking `waitpid`.

Continuous output can monopolize repeated UI callbacks; ANSI-heavy output can be more expensive than plain text. Closing a shell that delays exit can add an intentional half-second pause. The reader's synchronous callback provides backpressure, so this is not an unlimited posted-output queue; the concern is parsing cost and scheduling fairness.

**Recommendation:** Apply a measured per-tick parsing budget, coalesce repaint requests, and preserve backpressure. If parsing moves off-thread, give one owner responsibility for pyte state and send immutable display snapshots/deltas to the UI. Replace shutdown sleeps with asynchronous child monitoring and timed escalation.

**Validation:** Plain/ANSI-heavy output floods while typing in another pane, terminal resizing, and a child that ignores SIGHUP.

### 11. Medium: clipboard fallback can pause copy/kill for up to its timeout

**Evidence:** `src/candat/clipboard.py:49–82`: `_copy` immediately invokes `subprocess.run(..., timeout=1)` through `_copy_via_tool`. Although described as fire-and-forget, this channel waits for completion. Clipboard mode `all` extends the exposure to kill operations.

**Recommendation:** Dispatch the fallback asynchronously with bounded queued work and clear ordering between rapid copies. Keep the internal kill-ring update immediate. Cache configuration/tool discovery where useful. Large OSC 52 payload generation/output should be measured separately.

**Validation:** A fake clipboard tool that sleeps or never finishes must not delay the next key. The existing test fixture disables clipboard tools, so normal application tests do not exercise this latency.

### 12. Medium: navigation and project search need cancellation and bounded production

**Evidence:** `src/candat/nav.py:127–177` reads mutable `self._query` during a background walk; the 150 ms debounce starts workers without a latest-generation commit check. `_reveal_matches` can reload/expand many nodes. Fast edits to the filter can overlap walks and allow stale results to overwrite newer state. `MAX_MATCHES` is checked after a directory's filenames are processed, so a huge flat directory can greatly overshoot it.

`src/candat/projectsearch.py:34` captures all subprocess output and only later limits results to 500. A broad query can consume substantial time and memory despite the small final result list. `src/candat/dialogs.py`, `_candidates`, synchronously enumerates and sorts directory entries for completion.

**Recommendation:** Capture immutable query values, cancel/coalesce old walks, and reject stale results before installing or revealing them. Bound traversal and UI expansion work separately. Stream project-search results, stop the subprocess when the result limit is reached, and release it on cancellation. Load path completions asynchronously with bounded candidate lists.

**Validation:** Rapid filter changes/clear in a large tree, a huge flat directory, broad project searches, and slow mounted paths. Verify that the final visible result always corresponds to the latest query.

## Preview-specific follow-up

The preview design already does several things correctly: a worker renders strips, requests are coalesced, stale generations are checked, documents have a size cap, and the UI paints visible strips. Preserve this architecture.

Two remaining concerns merit profiling rather than an immediate rewrite (`src/candat/preview.py:96`, `markdown_chunks`; lines 202–245, render/finish):

- Chunks split only at suitable blank lines outside fences. A long paragraph, table, or code fence can remain one large rendering unit, delaying cancellation and the explicit worker yield.
- `_finish` reconstructs plain text for every rendered strip on the UI thread. Build that derivative data in the worker and hand over an immutable result if profiling shows a material completion spike.

Threaded rendering still competes for Python CPU. Consider process isolation only if measured event-loop delay remains poor after bounding chunks and editor highlighting. A process has serialization, startup, and memory costs; it should not be the default fix for every worker.

## Suggested implementation order

| Stage | Changes | Reason |
| --- | --- | --- |
| 1 | Add responsiveness measurements; repair terminal write/shutdown behavior; background growth indexing | Prevent indefinite waits and demonstrated multi-second stalls |
| 2 | Bound classic CSV work; redesign replace-all; background persistence and clipboard fallback | Remove command-triggered and periodic pauses |
| 3 | Cache/limit highlighting, table viewport reads, and result intersections | Improve sustained typing and scrolling |
| 4 | Navigation cancellation, streaming project search, preview refinements | Reduce stale work, memory peaks, and remaining completion spikes |

Keep Textual widget mutations on the UI thread. Use threads for blocking I/O; use bounded/cooperative computation or processes when CPU/GIL behavior demands it. Every worker needs a defined lifetime, a generation/revision check, bounded queued work, and an error/cancellation path. Moving a call to a worker while leaving equally expensive setup or completion on the UI thread is not sufficient.

## Performance validation plan

Add a dedicated benchmark/regression harness rather than timing arbitrary `pilot.pause()` calls. A 10–20 ms event-loop heartbeat should record lateness while representative actions run. Capture operation start, first feedback, completion, cancellation latency, peak RSS, and available worker count. For real terminal sessions, also measure key-to-paint latency: headless tests do not capture terminal transport or rendering overhead.

Proposed starting goals on a declared reference machine, to be calibrated rather than treated as established guarantees:

- Ordinary typing/navigation: p95 key-to-paint below 50 ms; p99 below 100 ms.
- Long jobs: acknowledge within 100 ms, with no sustained UI blackout; cancellation acknowledged within 100 ms and underlying work stopped within a documented bound.
- No routine background callback above 100 ms; aim for individual UI work batches around 5–10 ms.
- Stable bounded caches/queues during repeated searches, previews, and file changes.

Use a workload matrix covering 0.1/1/8 MB editor files; short versus very long lines; markdown with many fences and one large fence; one and multiple linked views; 50k/200k/millions of CSV rows; narrow/wide/multiline CSV; append bursts; slow I/O; terminal floods/pastes; many dirty tabs; and very large directory trees.

For intermittent freezes, collect timestamped stacks from an external sampler or watchdog that does not depend on the stalled event loop. Correlate samples with recovery ticks, disk polling, active commands, and worker completion. This will distinguish an actual blocked system call from CPU-heavy highlighting or rendering. Add targeted regression checks as fixes land, especially cancellation and data integrity during background saves; preserve current encoding, newline, atomic-write, undo, and linked-view behavior.

## Documentation accuracy

README claims that slow work never freezes and that table search/filtering are background operations are stronger than the current implementation supports. The classic CSV path is synchronous and capped at 200,000 loaded rows; multiline CSV can be routed there regardless of file size. After improvements, document which backend and guarantees apply. Until measured, avoid promising that a spinner or a thread makes all workloads responsive.
