# Performance implementation plan

Date: 2026-10-30
Follows: [performance-responsiveness-review.md](performance-responsiveness-review.md)
Target release: 1.7.1 — steps 1–9 implemented 2026-10-30

The aim is to remove UI hangs that can't be interrupted and the known
multi-second stalls with small, contained changes. Larger redesigns are
deferred (see the end). Each step ships with a regression test.

## Steps

| # | Review finding | Change | Test |
| - | --- | --- | --- |
| 1 | 1 (High) terminal writes | Writer thread per shell with an ordered queue; the UI thread never calls a blocking `os.write`. Queued bytes appear in the terminal title. `C-c` in the terminal discards queued input and sends SIGINT to the foreground process group. Input sent before the shell's first output is still held back (CI race fix from 0.2.x). | A child that never reads stdin plus a multi-MB `send_text`: the UI stays responsive, the queued count shows, and `C-c` clears it. Byte order is preserved when the child does read. |
| 2 | 11 clipboard | Run the clipboard tool on a single daemon thread with a latest-wins slot. The kill ring updates immediately. | A fake tool that sleeps 5 s: copy returns at once. |
| 3 | 3 (High) tail indexing | `LineIndex` scans of appended data get a wall-time budget (about 20 ms per tick) on top of the byte budget. Catch-up continues on later polls. | Appended 2-byte lines: each `check_disk` call returns within budget, and the index converges. |
| 4 | 5 (High) recovery | Recovery snapshots run in a worker thread, skip unchanged buffers (by edit generation), and never overlap. Cleanup on clean exit waits for any write still in flight. | Snapshot with a slow writer: the tick returns quickly, and an unchanged buffer is not rewritten. |
| 5 | 4 (High) replace-all | `!` finds the remaining matches once on a single text snapshot and applies them in one edit (one undo step, not one per match), keeping smart-case and the from-point semantics. | 5,000 replacements: correct result, one undo restores, fast. |
| 6 | 6 markdown typing | Cache inline highlights per line string and the fence scan per document; reuse block parses (already cached). | Highlight map rebuild on a 7k-line document is close to the plain baseline when cached. |
| 7 | 2 (High) classic CSV | Stopgap: route to the windowed BigTable at a lower cost estimate (rows × columns), so the classic synchronous table only handles small files. Multiline-quoted files keep the classic path, as the review notes. | Routing test on a 50k-row file → BigTable. |
| 8 | Diagnosis | Opt-in stall watchdog (`CANDAT_WATCHDOG=1`): a thread pings the event loop and logs the UI thread's stack to `~/.local/state/candat/stalls.log` when a ping is late by more than 0.5 s. | The watchdog records a deliberate stall. |
| 9 | Docs | README: replace "never freezes" and "background" claims with accurate wording, and document the watchdog. | — |

## Deferred (bigger redesigns, measure first with the watchdog)

- A cancellable, batched pipeline for classic CSV (finding 2, full fix), and BigTable viewport caching and bounded reads (7).
- A regex engine with per-match limits / process isolation (8), and off-thread intersections (9).
- pyte parsing budget and async shutdown (10), and nav/project-search cancellation and streaming (12).
- Splitting the preview chunks further.
