# Week 6 — protocol evidence

Generated 2026-10-05T16:17:04+00:00 by `python experiments/week6_eval.py status`.

| file | what | first commit | committed at | commits | on disk matches HEAD |
|---|---|---|---|---|---|
| `eval_cases.jsonl` | 25 mode-tagged cases | `33ab15d` | 2026-09-15T15:40:23+05:30 | 1 | yes |
| `judge_v0_presplit.txt` | judge before the assertion split | `33ab15d` | 2026-09-15T15:40:23+05:30 | 1 | yes |
| `judge_v1.txt` | judge after the split (1 criterion) | `d75cacb` | 2026-09-15T15:40:47+05:30 | 1 | yes |
| `replies_25.json` | frozen replies | `b594458` | 2026-09-15T16:04:14+05:30 | 1 | yes |
| `labels_25.json` | blind hand labels | — | — | 0 | not on disk |
| `judge_results_v1.json` | judge v1 run -> agreement_before | — | — | 0 | not on disk |
| `prediction.txt` | prediction before iterating | — | — | 0 | not on disk |
| `judge_v2.txt` | judge with 2 of v1's disagreements | — | — | 0 | not on disk |
| `judge_results_v2.json` | judge v2 run -> agreement_after | — | — | 0 | not on disk |

## Ordering checks

- n/a  labels committed before the judge v1 result: not both committed yet
- n/a  prediction committed before judge v2 exists: not both committed yet
- OK   pre-split judge before the split judge: 33ab15d (2026-09-15T15:40:23+05:30) before d75cacb (2026-09-15T15:40:47+05:30)
