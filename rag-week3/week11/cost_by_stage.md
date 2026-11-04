# Week 11 — Cost by stage

Generated 2026-10-25T11:05:04+00:00 by `python experiments/week11_report.py` from the
Week 11 index over `traces/traces.jsonl`. The same numbers are live in *Observability → Cost & usage*.

**Measurement window:** 2026-09-07T13:12:00.765+00:00 → 2026-10-25T11:04:47.687+00:00 · 364 requests · 160 with complete token usage ·
204 without usage (counted, not priced) · controlled incidents included.

## What is measured and what is estimated

- **Tokens** are measured by the application with the model's own tokenizer (input on the rendered chat template, output
  re-tokenised from the reply). The models run in-process; there is no provider usage field and no provider bill.
- **Cost** is an **estimate**: tokens × the stand-in rate card the repo has used since Week 7
  (`agent_runtime.RATE_CARD`: $0.15 per M input tokens, $0.6 per M output tokens, USD).
- **Retrieval** (embedding, pgvector, BM25, cross-encoder) is **not separately metered**; it is local compute measured as time only, and enters the total as $0.
- **Validation** (distance gate, grounding, assertions) and **tools** (policy lookup, MCP tools) are code with no model call: $0.
- Records written before Week 11 carry an output token count but no input count; their cost is reported as *not computable*, never guessed.

## Cost per query

- cost: mean $0.000200 · p50 $0.000168 · p95 $0.000535 · max $0.000802 (n=160)
- input tokens: mean 587 · p50 597 · p95 1994 · max 3411 (n=160)
- output tokens: mean 111 · p50 115 · p95 379 · max 531 (n=160)
- latency (all requests, end to end): mean 28.64 s · p50 5.03 s · p95 102.82 s · max 1004.23 s (n=364)
- retries recorded: 2 (retries are recorded per request from Week 11 on; multi-agent records carry meter.retries)

## Stage breakdown

| stage | cost (USD) | metered? | basis | time from spans |
|---|---|---|---|---|
| retrieval | $0 (unmetered) | no | not separately metered: embedding, pgvector, BM25 and the cross-encoder run locally; measured as time only | 346.35 s over 83 spans |
| generation | $0.024782 | estimate | estimate: locally counted tokens x stand-in rate card (USD 0.15 per M input, 0.60 per M output; GPT-4o-mini class). The model runs in-process, so no provider charge exists. | 2841.61 s |
| validation | $0 | code only | gates, grounding and assertions are code; no model, no charge | 1.73 s |
| tools | $0 | no charge | local function calls (policy lookup, MCP tools); no per-call charge | 0.0 ms |
| **total attributable** | **$0.024782** | | total = generation only; generation = Σ(input_tokens) x 0.15/1e6 + Σ(output_tokens) x 0.60/1e6 | |

Generation tokens in the window: 93858 in / 17839 out.
Stage times come from the 83 requests recorded with Week 11 spans; the other
281 carry end-to-end latency only.

**How the total is calculated:** total = Σ input_tokens × 0.15 / 1e6 + Σ output_tokens × 0.60 / 1e6, over requests with
both counts; retrieval, validation and tools add 0 because nothing meters them.

### By model / provider

| | requests | with usage | cost mean | cost total | tokens in / out | latency p50 | latency p95 |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen2.5-0.5B-Instruct` | 202 | 44 | $0.000000 | $0.000084 | 542 / 4 | 1.22 s | 11.03 s |
| `Qwen/Qwen2.5-1.5B-Instruct` | 162 | 116 | $0.000200 | $0.024698 | 93316 / 17835 | 30.58 s | 193.00 s |

### By request type

| | requests | with usage | cost mean | cost total | tokens in / out | latency p50 | latency p95 |
|---|---|---|---|---|---|---|---|
| `answer` | 177 | 42 | $0.000000 | $0.000084 | 542 / 4 | 1.23 s | 11.91 s |
| `ticket_reply` | 157 | 88 | $0.000200 | $0.013390 | 49741 / 9881 | 26.05 s | 104.24 s |
| `week10_multi_agent` | 30 | 30 | $0.000400 | $0.011309 | 43575 / 7954 | 81.03 s | 544.37 s |

### By prompt version

| | requests | with usage | cost mean | cost total | tokens in / out | latency p50 | latency p95 |
|---|---|---|---|---|---|---|---|
| `ticket-v1.0` | 25 | 2 | $0.000000 | $0.000000 | 0 / 0 | 1.08 s | 7.55 s |
| `ticket-v1.2` | 104 | 58 | $0.000100 | $0.008623 | 30920 / 6642 | 28.94 s | 104.24 s |
| `ticket-v1.3` | 28 | 28 | $0.000200 | $0.004767 | 18821 / 3239 | 29.09 s | 135.97 s |
| `v1.0` | 177 | 42 | $0.000000 | $0.000084 | 542 / 4 | 1.23 s | 11.91 s |
| `week10-multi-v1.0` | 11 | 11 | $0.000400 | $0.004104 | 15503 / 2965 | 92.75 s | 331.63 s |
| `week10-multi-v1.1` | 19 | 19 | $0.000400 | $0.007204 | 28072 / 4989 | 81.03 s | 1004.23 s |

Cost outlier threshold (p95 of complete costs): $0.000535.
