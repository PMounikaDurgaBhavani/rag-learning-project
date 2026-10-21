# Week 10 — race table: single agent vs manager + two specialists

Generated 2026-10-21T13:19:41+00:00 from `week10/runs/single_20261021-083852.json` and `week10/runs/multi_20261021-082404.json` (build `7e44499`). Same 10 cases, same model, same retrieval, same judge.

| metric | single agent | multi-agent | note |
|---|---|---|---|
| pass rate | **2/10 = 20.0%** | **3/10 = 30.0%** | assertions AND judge |
| judge PASS (model-based) | 3/10 | 5/10 | judge v2, agreement with blind labels 76.0% (held-out 78.3%) |
| assertions pass (deterministic) | 5/10 | 5/10 | 6 code checks |
| citations inside the reference articles | 0/0 | 0/0 | deterministic; denominator = replies that cite |
| refused / not found | 1 / 1 | 1 / 1 | routed hand-off |
| degraded (specialist failure) | 0 | 0 | |
| p50 latency | 80.9 s | 80.2 s | median, sequential, warm |
| p95 latency | 104.3 s | 216.2 s | nearest-rank: the slowest case at n=10 |
| p99 latency | 104.3 s | 216.2 s | same case as p95 at n=10 |
| mean latency | 67.8 s | 76.7 s | |
| input tokens | 5,665 | 17,199 | summed per model call |
| output tokens | 1,343 | 3,128 | |
| total tokens | **7,008** (700.8/case) | **20,327** (2032.7/case) | |
| cost per case | $0.000166 | $0.000446 | stand-in rate card $0.15/$0.6 per Mtok, not a provider charge |
| total cost | $0.001657 | $0.004456 | |
| model calls | 9 (0.9/case) | 33 (3.3/case) | |
| hand-offs | 0 | 28 (2.8/case) | dispatch + return, manager ↔ specialist |
| specialist calls | 0 | 14 | |
| re-synthesis (conflict resolution) | — | 1 | bounded to 1 per case |
| context re-send multiplier | — | 38.75× | total prompt tokens / ticket tokens, mean |
| errors / retries / timeouts | 0 / 0 / 0 | 0 / 0 / 0 | |

## Pass rate by Week 5 mode

| mode | n | single | multi |
|---|---|---|---|
| M1 | 2 | 1/2 | 1/2 |
| M2 | 2 | 0/2 | 0/2 |
| M3 | 2 | 1/2 | 1/2 |
| M4 | 2 | 0/2 | 0/2 |
| M5 | 2 | 0/2 | 1/2 |

## Per case

| case | mode | single | multi | single s / tok | multi s / tok | multi calls / hand-offs | winner |
|---|---|---|---|---|---|---|---|
| T01 | M1 | PASS | PASS | 38.1 / 769 | 25.9 / 1653 | 3 / 2 | both |
| T03 | M1 | FAIL | FAIL | 102.2 / 830 | 81.4 / 2654 | 4 / 4 | neither |
| R02 | M2 | FAIL | FAIL (not found) | 2.3 / 0 | 4.2 / 186 | 1 / 2 | neither |
| T07 | M2 | FAIL | FAIL | 91.8 / 831 | 79.0 / 2792 | 4 / 4 | neither |
| R03 | M3 | FAIL | PASS | 104.3 / 744 | 32.9 / 1537 | 3 / 2 | multi |
| T11 | M3 | PASS | FAIL | 25.9 / 696 | 91.3 / 1708 | 3 / 2 | single |
| R04 | M4 | FAIL | FAIL | 53.4 / 757 | 59.6 / 1631 | 3 / 2 | neither |
| T16 | M4 | FAIL | FAIL | 97.7 / 820 | 84.6 / 2683 | 4 / 4 | neither |
| T18 | M5 | FAIL | FAIL | 88.2 / 715 | 91.4 / 1588 | 3 / 2 | neither |
| T21 | M5 | FAIL | PASS | 73.6 / 846 | 216.2 / 3895 | 5 / 4 | multi |

Wins: {'single': 1, 'multi': 2, 'both': 1, 'neither': 6}.

Largest token-consuming hand-off: **manager (synthesis) on T21**, 2401 tokens (in 2188 / out 213), 151.6 s. Task `task-86e1915138`.

Cold start (model load, charged to no case): single 70.8 s, multi 70.3 s.
