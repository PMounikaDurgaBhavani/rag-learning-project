# Cost per deflected ticket and the 10× plan — Week 12

Generated 2026-11-04T12:34:29Z from `week12/cost_and_scaling.json` (run `week12/runs/v1.1_20261104-143011.json`, 40 requests, 12 clean deflections = 30.0%).

## Measured

- Machine: 8 GB Apple M1 laptop, MPS fp16, one request at a time; drafter + embedder + reranker in process
- Latency per request: p50 96.2 s · p95 381.7 s · mean 145.5 s; generation p50 123.8 s; MCP search p50 7.4 s
- Tokens: 24245 in / 3375 out, 690.5 per request, 0.85 model calls per request

## Cost per request and per deflected ticket

| basis | per request | per deflected ticket |
|---|---|---|
| local compute (0.35 USD/h × measured latency) + embedding + infra share | $0.02915 | **$0.09717** |
| hosted-API stand-in rate card (0.15/0.6 USD per Mtok) + embedding + infra share | $0.01514 | $0.05048 |
| human Tier-1 handling (8 min at 28.0 USD/h loaded) | — | $3.73 |

- Breakdown (local basis, per request): compute $0.01415, embedding $0.000001, fixed infra share $0.01500 (Postgres 15.0 + MCP 10.0 + observability 5.0 USD/month over 2,000 tickets), tools $0 (order/entitlement/escalation are local calls (<5 ms); the MCP server is in the fixed share)
- Local models have no API charge, but they are not free: the compute line is the measured wall time of each request at the machine rate. 0.35 USD/h stands in for an on-prem Apple-silicon box or a small GPU VM (A10-class spot ~0.3-0.6 USD/h); the measured latency is from an 8 GB M1 laptop, which is slower than either
- Retrieval cost: embedding of the query and the cross-encoder run inside the request's wall time (already in the compute line); pgvector and BM25 are sub-10 ms.

## Load: 2,000 tickets/month vs Black Friday 20,000 tickets/week

| | current | Black Friday |
|---|---|---|
| tickets | 2000/month | 20000/week |
| requests/s (average) | 0.0008 | 0.0331 |
| requests/s (peak ×3.0) | 0.0023 | 0.0992 |
| concurrent requests at mean latency (peak) | 0.34 | 14.44 |
| concurrent requests at p95 latency (peak) | 0.88 | 37.87 |

## Capacity and the first failure point

- Model: one in-process model behind the server's single lock (serial); mean latency 145.5 s, p95 381.7 s → serial ceiling **24.7 requests/hour** (594.0/day)
- Workers needed to keep up at peak: current 0.33, Black Friday 14.44 → run **22 workers** (×1.5 headroom)
- Retrieval: pgvector 13 docs / 189 chunks: exact scan < 10 ms; at 10x corpus still < 50 ms with HNSW; BM25 in-memory index rebuilt per process; reranker cross-encoder ~100-300 ms per query on CPU
- MCP server: one uvicorn process; the embedder and reranker load once; one search ~0.2-1 s warm; stateless HTTP scales horizontally
- Rate limits: none for local models; on the API basis 20k tickets/week x ~1.3k tokens = ~26M tokens/week, inside typical tier limits but needs retries/backoff
- **First expected failure point: the serial model lock: at the Black Friday peak the queue grows faster than one worker drains it**

## The 10× plan

1. Run the drafter as a worker pool (N = peak RPS x mean latency x 1.5) behind a queue; the server already returns 409 instead of queueing, so add a job queue (Redis/RQ or Celery) and poll for the result
2. Move generation off the 1.5B local model to a hosted endpoint or a GPU box; measured mean latency is the lever, not token cost
3. Keep the MCP help-centre server stateless and run 2+ replicas; cache search results per (query, policy_version) for 5 minutes
4. Postgres: HNSW index on chunks.embedding, pooled connections, read replica for retrieval
5. Observability: rotate traces.jsonl daily; the SQLite index becomes Postgres once >1 writer exists
6. Pre-check the cheap deterministic paths (sensitive intents escalate in <50 ms with no model) so they never wait behind generation

<!-- HAND-WRITTEN: start -->
## Notes (written 2026-11-04)

**Read the latency with its caveat.** The measured mean of 145.5 s per request comes from a 40-case run on an 8 GB laptop that was swapping under Android Studio, a JVM, Teams and Chrome at the same time; nine replies were cut off by the generation deadline. The smoke runs on the same machine when quiet took 30–90 s in fast mode. Every capacity number above scales linearly with that mean: halve the latency and the worker count halves.

**Cost per deflected ticket is dominated by clean-deflection rate, not by tokens.** Tokens cost $0.00014 per request on the stand-in rate card; what makes a deflected ticket cost about $0.10 (local basis) or $0.05 (API basis) is that only 12 of 40 cases were clean deflections (answered, right decision, every assertion passing) in this run, because of the cut-off replies. With the same decisions and uncut replies (the 31 answered cases), the same arithmetic gives roughly $0.04 and $0.02. Either way it is two orders of magnitude under the $3.73 human handling estimate, and the human cost is the number that justifies the project, not the token bill.

**Local models are not free.** The compute line charges the machine's wall time at 0.35 USD/h. A faster machine lowers it twice: fewer seconds per request and fewer workers at peak.

**Black Friday arithmetic, spelled out.** 20,000 tickets in a week is 0.033 requests/s on average; with a ×3 business-hours peak, 0.1 requests/s. At the measured mean latency each request occupies a worker for 145 s, so the peak needs 14.4 concurrent workers (38 at p95). One in-process model behind one lock serves 24.7 requests/hour; the peak offers 357 per hour. **The lock is the first failure point**, by a factor of about 15, long before Postgres (sub-10 ms queries), the MCP server (0.2–1 s per search, stateless), or any token quota (none for local models; ~26M tokens/week on the API basis).

**What the plan does not assume.** No model change is required for correctness: the decisions are code and the assertions are the regression net. The plan buys latency and concurrency; it does not need to buy a smarter planner, because tool choice was 31 of 32 in the trajectory run.
<!-- HAND-WRITTEN: end -->
