# Week 10 — race table: single agent vs manager + two specialists

Generated 2026-10-21T02:53:42+00:00 from `week10/runs/single_20261021-001651.json` and `week10/runs/multi_20261021-001651.json` (build `7e44499`). Same 10 cases, same model, same retrieval, same judge.

| metric | single agent | multi-agent | note |
|---|---|---|---|
| pass rate | **2/10 = 20.0%** | **3/10 = 30.0%** | assertions AND judge |
| judge PASS (model-based) | 3/10 | 4/10 | judge v2, agreement with blind labels 76.0% (held-out 78.3%) |
| assertions pass (deterministic) | 5/10 | 7/10 | 6 code checks |
| citations inside the reference articles | 0/0 | 0/0 | deterministic; denominator = replies that cite |
| refused / not found | 1 / 1 | 4 / 4 | routed hand-off |
| degraded (specialist failure) | 0 | 0 | |
| p50 latency | 79.1 s | 98.7 s | median, sequential, warm |
| p95 latency | 130.3 s | 331.6 s | nearest-rank: the slowest case at n=10 |
| p99 latency | 130.3 s | 331.6 s | same case as p95 at n=10 |
| mean latency | 81.3 s | 142.9 s | |
| input tokens | 5,665 | 14,075 | summed per model call |
| output tokens | 1,343 | 2,740 | |
| total tokens | **7,008** (700.8/case) | **16,815** (1681.5/case) | |
| cost per case | $0.000166 | $0.000375 | stand-in rate card $0.15/$0.6 per Mtok, not a provider charge |
| total cost | $0.001657 | $0.003754 | |
| model calls | 9 (0.9/case) | 30 (3.0/case) | |
| hand-offs | 0 | 28 (2.8/case) | dispatch + return, manager ↔ specialist |
| specialist calls | 0 | 14 | |
| re-synthesis (conflict resolution) | — | 1 | bounded to 1 per case |
| context re-send multiplier | — | 29.94× | total prompt tokens / ticket tokens, mean |
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
| T01 | M1 | PASS | PASS | 96.3 / 769 | 146.4 / 1653 | 3 / 2 | both |
| T03 | M1 | FAIL | FAIL | 73.9 / 830 | 193.0 / 2388 | 4 / 4 | neither |
| R02 | M2 | FAIL | FAIL (not found) | 8.3 / 0 | 9.4 / 186 | 1 / 2 | neither |
| T07 | M2 | FAIL | FAIL | 84.3 / 831 | 323.6 / 2615 | 4 / 4 | neither |
| R03 | M3 | FAIL | PASS | 70.8 / 744 | 51.0 / 1537 | 3 / 2 | multi |
| T11 | M3 | PASS | FAIL (not found) | 71.5 / 696 | 30.6 / 837 | 2 / 2 | single |
| R04 | M4 | FAIL | FAIL (not found) | 130.3 / 757 | 16.9 / 779 | 2 / 2 | neither |
| T16 | M4 | FAIL | FAIL | 59.9 / 820 | 331.6 / 2455 | 4 / 4 | neither |
| T18 | M5 | FAIL | FAIL (not found) | 92.2 / 715 | 25.5 / 772 | 2 / 2 | neither |
| T21 | M5 | FAIL | PASS | 125.7 / 846 | 300.6 / 3593 | 5 / 4 | multi |

Wins: {'single': 1, 'multi': 2, 'both': 1, 'neither': 6}.

Largest token-consuming hand-off: **manager (synthesis) on T21**, 2099 tokens (in 1839 / out 260), 244.5 s. Task `task-69e17353a3`.

Cold start (model load, charged to no case): single 87.2 s, multi 87.2 s.
