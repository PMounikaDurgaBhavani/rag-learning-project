# Week 11 — 10× capacity assessment

Generated 2026-10-25T11:05:04+00:00 by `python experiments/week11_report.py`; live in *Observability → 10× capacity*.

**Observed window:** last 90 days → 2026-09-07T13:12:00.765+00:00 to 2026-10-25T11:04:47.687+00:00
(47.91 days spanned) · 362 requests · 225 model-backed answered requests · controlled incidents excluded.

| | current | projected 10× |
|---|---|---|
| requests per day | 7.56 | 75.56 |
| peak recorded hour (2026-09-07T13Z) | 94 requests | 940 requests |
| tokens (in + out) | 110019 | 1100190 |
| cost over the window (estimate, stand-in rate card) | $0.024394 | $0.243945 |

**Latency, answered model-backed requests (n=225):** p50 22.48 s · p95 125.54 s · p99 331.63 s · mean 41.58 s.
Sample size supports the percentiles (n ≥ 20).

**Capacity model:** serial: one model-backed request at a time (server RUN_LOCK). Mean latency 41.58 s → ceiling 86.6 requests/hour,
2077.7 requests/day. Utilisation now 0.0036 → at 10× 0.0364.
Provider request/token limits: not applicable (local models); none configured or verified.

## Which breaks first

**latency / throughput (one in-process model behind a single lock)**

Latency breaks first: the drafter answers one request at a time at roughly 42 s each, so a tenfold peak queues behind the lock long before the stand-in cost ($0.2439) or any provider quota matters.

Evidence:
- peak recorded hour 2026-09-07T13Z: 94 requests; x10 = 940; serial ceiling at mean latency 41.6 s = 87 requests/hour
- p95 latency 125.5 s, p99 331.6 s over n=225 answered requests
- cost over the window: $0.024394 (158 requests with usage); x10 = $0.243945
- rate limits: none configured — the models run in-process; the only quota is the server's one-run-at-a-time lock (HTTP 409 to a second concurrent model request)

Assumptions:
- workload scales uniformly: 10x the requests with the same mix of chat answers, ticket replies and multi-agent runs
- latency stays at the recorded mean (no warm-up effects removed: the first request of a server session includes model loading)
- cost is the stand-in rate card on recorded tokens; the local deployment has no provider bill
- arrivals in the peak hour are served serially behind the single model lock

**Mitigation:** Run the drafter as a separate worker pool (2–4 processes, or a hosted endpoint with a real quota) so requests are no longer serialised behind one lock, and cache the retrieval stage per query; re-measure p95 before raising the ceiling.

**One line:** Latency breaks first: the drafter answers one request at a time at roughly 42 s each, so a tenfold peak queues behind the lock long before the stand-in cost ($0.2439) or any provider quota matters.
