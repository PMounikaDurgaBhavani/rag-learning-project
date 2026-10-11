# CloudDesk RAG — Week 4: Retrieval Failure Analysis & Improvement Report

> **Revision note (2026-10-11).** An audit of the original Week 4 report found
> that four of the twelve golden-set labels pointed at a chunk that does not
> state the answer. This revision corrects those labels, re-runs the *same*
> single-change experiment unchanged, and restates every number from the
> corrected run. The Week 3 report this file replaced is preserved in git at
> commit `48e93d7`.

## 1. Executive Summary

The Week 3 dense retriever is compared against **one retrieval change** —
BM25 lexical search fused with the dense ranking by Reciprocal Rank Fusion —
on a 12-question golden set. Nothing else moves between the two arms: same
index, same `markdown` chunking (300 / 50), same 25-candidate pool, same k=3,
no reranker.

| Metric | Baseline (Week 3 dense) | Improved (dense + BM25 · RRF k=60) | Delta |
| :--- | :---: | :---: | :---: |
| **Hit-rate@3** (corrected labels) | **66.7% (8/12)** | **83.3% (10/12)** | **+16.7 pts** |
| Baseline R-failures fixed | — | **2 / 4** | — |
| Regressions | — | **0** | — |
| Rank-1 accuracy on hits | 4 / 8 (50.0%) | 6 / 10 (60.0%) | +10.0 pts |
| **p50 latency** (authoritative Week 4 run, commit `efd07ba`) | **12.63 ms** | **13.09 ms** | +0.46 ms, inside the ±0.63 ms noise band |
| **Shipping decision** | — | **SHIP** | — |

Source for every number below: [evaluation/golden_ab_results.json](evaluation/golden_ab_results.json),
regenerated on 2026-10-11 by `experiments/evaluate_golden_ab.py` with no code
changes. Latency is discussed separately in §6 because the saved files disagree.

---

## 2. 12-Question Golden Set

[evaluation/golden_set.jsonl](evaluation/golden_set.jsonl). Six of the twelve
questions contain an exact identifier (`HC-001` … `HC-006`), against a required
minimum of four. Expected chunk IDs were declared before any retrieval ran.

| ID | Question | Expected `chunk_id` | Article | Exact token? | Key fact |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **GS-01** | How long is a password reset link valid in CloudDesk? | `HC-001-markdown-5` | HC-001 | No | 30 minutes |
| **GS-02** | What are the minimum character requirements for a new password under HC-001? | `HC-001-markdown-6` | HC-001 | **Yes** | 8 characters |
| **GS-03** | What is the troubleshooting solution when the reset link has expired? | `HC-001-markdown-11` | HC-001 | No | Request a new reset link |
| **GS-04** | How long does a temporary CloudDesk account lock last after failed logins? | `HC-002-markdown-6` *(corrected)* | HC-002 | No | 15 minutes |
| **GS-05** | What should I do if an account is locked due to incorrect password attempts in HC-002? | `HC-002-markdown-10` | HC-002 | **Yes** | Wait 15 minutes |
| **GS-06** | How do I update an expired payment method in CloudDesk? | `HC-003-markdown-8` *(corrected)* | HC-003 | No | Organization Settings › Billing › Payment Methods |
| **GS-07** | My card expired and payment failed under HC-003. What is the solution? | `HC-003-markdown-13` | HC-003 | **Yes** | Update the payment method |
| **GS-08** | How do I filter CloudDesk tickets by Priority in HC-004? | `HC-004-markdown-8` | HC-004 | **Yes** | Priority filter |
| **GS-09** | What is the cause if CloudDesk notifications stopped suddenly? | `HC-005-markdown-16` *(corrected)* | HC-005 | No | Organization settings changed |
| **GS-10** | Who can manage organization-level notification controls in HC-005? | `HC-005-markdown-23` | HC-005 | **Yes** | CloudDesk administrator |
| **GS-11** | What is the maximum individual file size limit for CloudDesk uploads? | `HC-006-markdown-3` | HC-006 | No | File size limit |
| **GS-12** | Why is the Upload File button unavailable according to HC-006 troubleshooting? | `HC-006-markdown-11` *(corrected)* | HC-006 | **Yes** | User lacks required permission |

### 2.1 Label corrections

**Rule applied:** a label changes only when the labelled chunk does not state
the answer, and the replacement is the chunk in the same article section that
does. The rule does not look at retrieval output, so it cannot favour either
arm. Each replacement was checked against the chunk text in the live index
before the label was changed.

| ID | Old label | What the old chunk actually says | New label | What the new chunk says |
| :--- | :--- | :--- | :--- | :--- |
| GS-04 | `HC-002-markdown-5` | The lock lasts "a short period" — no duration | `HC-002-markdown-6` | "The temporary lock period is 15 minutes." |
| GS-06 | `HC-003-markdown-9` | "update it before the next billing attempt" — no steps; the old answer key "Billing Settings" appears nowhere in the corpus | `HC-003-markdown-8` | The update steps: Organization Settings › Billing › Payment Methods › select › enter › save |
| GS-09 | `HC-005-markdown-12` | The `Notification goes to spam` table row | `HC-005-markdown-16` | The `Notifications stopped suddenly \| Organization settings changed` row |
| GS-12 | `HC-006-markdown-9` | The `File upload rejected \| File type is not supported` row | `HC-006-markdown-11` | The `Upload button unavailable \| User lacks required permission` row |

In every case the section split at 300 tokens and the label landed on the
wrong half. The original report quoted table rows for GS-09 and GS-12 that the
labelled chunks do not contain.

---

## 3. Baseline Failure Inspection & Classification

All 12 questions through the Week 3 dense retriever (`all-MiniLM-L6-v2`,
cosine, Top-3). Ranks are positions in the 25-candidate dense list.

- **Baseline hit-rate@3: 8 / 12 (66.7%)**

### Failure tally

```
R (Retrieval)           : 4   — the gold chunk exists and was ranked 4th–9th
G (Generation)          : not assessed — no generation step runs in this experiment
Not-In-Corpus           : 0   — every gold chunk is in the index and in the top-25 candidates
```

`G` is not reported as zero. This experiment measures retrieval only; the
generator never runs, so no claim about generation failures is made. The code
labels a miss `Not-In-Corpus` only when the gold chunk is absent from the
top-25 dense candidates, and with corrected labels that never happens.

### Evidence for each miss

1. **GS-01 (R)** — expected `HC-001-markdown-5` ("The password reset link is
   valid for 30 minutes") ranked **#8**. Top-3: `-15, -6, -18`. The FAQ chunk
   `-15` at rank 1 also states "valid for 30 minutes"; see §5.1.
2. **GS-04 (R)** — expected `HC-002-markdown-6` ("The temporary lock period is
   15 minutes") ranked **#9**. Top-3: `-20, -5, -1`. The FAQ chunk `-20` at
   rank 1 also states "lasts 15 minutes"; see §5.1.
3. **GS-05 (R)** — expected `HC-002-markdown-10` (the
   `Account locked after failed attempts \| Wait 15 minutes` row) ranked **#7**.
   Top-3: `-7, -4, -3` — a "Follow these steps:" stub and two overview
   paragraphs, none of which states the fix.
4. **GS-06 (R)** — expected `HC-003-markdown-8` (the update steps) ranked
   **#4**. Top-3: `-4, -9, -6` — supported payment methods, the one-line
   "update it before the next billing attempt", and the *add* (not update)
   steps.

Three of the four misses are terse table rows or numbered steps, and two of
the questions carry an exact identifier the embedding treats as ordinary text.
That pointed at one fix.

---

## 4. The Chosen Retrieval Improvement: BM25 + RRF

Dense search and BM25 are each asked for 25 candidates over the same chunks.
The two ranked lists are fused by position, not score, so no normalisation is
needed between a cosine distance and a BM25 score:

```
RRF_score(d) = 1 / (60 + rank_dense(d)) + 1 / (60 + rank_bm25(d))
```

### The one change, as it exists in code

The two arms are defined in [src/golden_eval.py](src/golden_eval.py) (lines
60–68). This is the real code, not an illustration:

```python
if arm == "baseline":
    return retrieve_chunks(query, top_k=candidate_k, strategy=strategy)

dense  = retrieve_chunks(query, top_k=candidate_k, strategy=strategy)
sparse = retrieve_bm25(query,   top_k=candidate_k, strategy=strategy)
return reciprocal_rank_fusion(dense, sparse, k_constant=RRF_K)   # RRF_K = 60
```

Unchanged between arms: embedding model, chunking strategy, chunk size,
overlap, index, candidate pool (25), k (3), golden set. The cross-encoder
reranker ([src/reranker.py](src/reranker.py)) was built and benchmarked
separately ([evaluation/hybrid_reranking_results.json](evaluation/hybrid_reranking_results.json),
on the Week 3 question set) but is **not** in either arm.

---

## 5. Per-Question Results: Baseline vs. Improved

Ranks are positions in the 25-candidate list; "miss" means outside the top 3.

| ID | Expected chunk | Baseline | Improved | Status |
| :--- | :--- | :---: | :---: | :--- |
| **GS-01** | `HC-001-markdown-5` | miss (#8) | **#2** | **FIXED** |
| **GS-02** | `HC-001-markdown-6` | #1 | #2 | maintained |
| **GS-03** | `HC-001-markdown-11` | #1 | #1 | maintained |
| **GS-04** | `HC-002-markdown-6` | miss (#9) | miss (#4) | UNFIXED — moved from #9 to #4, still outside top-3 |
| **GS-05** | `HC-002-markdown-10` | miss (#7) | miss (#8) | UNFIXED |
| **GS-06** | `HC-003-markdown-8` | miss (#4) | **#2** | **FIXED** |
| **GS-07** | `HC-003-markdown-13` | #1 | #1 | maintained |
| **GS-08** | `HC-004-markdown-8` | #3 | **#1** | promoted |
| **GS-09** | `HC-005-markdown-16` | #2 | **#1** | promoted |
| **GS-10** | `HC-005-markdown-23` | #3 | **#1** | promoted |
| **GS-11** | `HC-006-markdown-3` | #1 | #1 | maintained |
| **GS-12** | `HC-006-markdown-11` | #2 | #2 | maintained |

- **Fixed:** GS-01, GS-06
- **Unfixed:** GS-04, GS-05
- **Regressed:** none
- **Promoted to rank 1:** GS-08, GS-09, GS-10

Two things the relabelling changed, stated plainly: the GS-06 correction
*created* a fix for the hybrid arm (the old label was a hit in both arms), and
the GS-04 correction *created* a miss in both arms (the old label was a hit in
both). GS-09 and GS-12, previously counted as unfixed failures, were hits in
both arms all along.

### 5.1 Sensitivity check — any answer-bearing chunk

The primary metric credits exactly one gold chunk. Several articles repeat an
answer verbatim in an FAQ section, so a second scoring was run that also
credits those chunks: GS-01 also accepts `HC-001-markdown-15`, GS-04 also
accepts `HC-002-markdown-20`, GS-05 also accepts `HC-002-markdown-8`, GS-12
also accepts `HC-006-markdown-20` and `-24`.

| Scoring | Baseline | Improved | Delta |
| :--- | :---: | :---: | :---: |
| Single gold chunk (primary) | 8/12 | 10/12 | +2 |
| Any answer-bearing chunk | 10/12 | 11/12 | +1 |

Under the looser scoring GS-01 and GS-04 are already answered at rank 1 by the
FAQ chunk in the baseline, so the hybrid's gain narrows to GS-06 alone. GS-05
is the only question that fails under both scorings in both arms: no chunk
stating "wait 15 minutes" reaches the top 3 for the conversational phrasing
"locked due to incorrect password attempts".

---

## 6. Latency Analysis

Latency does not depend on labels, so it was not re-measured for this revision.
Three saved measurements exist and they disagree; this is why:

| Run | Baseline p50 | Improved p50 | Method | Status |
| :--- | :---: | :---: | :--- | :--- |
| `evaluation/week4_golden_evaluation.json` (commit `124b9a3`, 2026-09-02) | 17.97 ms | 11.35 ms | One pass per arm, no warm-up, baseline ran first and paid model-load and collection-open costs | **Historical. The "hybrid is faster" result is a warm-up artefact — hybrid does strictly more work.** |
| `evaluation/golden_ab_results.json` as committed in `efd07ba` (2026-09-07) | **12.63 ms** | **13.09 ms** | Full warm-up of both arms, then 5 interleaved repeats per question, median per question; Week 4 ChromaDB index | **Authoritative Week 4 measurement.** Delta +0.46 ms, inside the ±0.63 ms noise band. |
| `evaluation/golden_ab_results.json` as regenerated (2026-09-08 in `c152efb`, again 2026-10-11) | 28.5 ms | 28.74 ms | Same method, but the vector store moved to PostgreSQL + pgvector in Week 5 | Post-Week-4 backend; not a Week 4 number. Delta +0.24 ms, inside the ±1.43 ms noise band. |

The defensible Week 4 claim is that the change has **no measurable latency
cost**. The earlier claim that "BM25 pre-scoring enables fast candidate
pruning" is withdrawn; the hybrid arm runs the dense query *and* a BM25 pass.

To reproduce the authoritative numbers from git:

```bash
git show efd07ba:rag-week3/evaluation/golden_ab_results.json \
  | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['baseline']['p50_latency_ms'],d['improved']['p50_latency_ms'],d['p50_noise_band_ms'])"
# 12.63 13.09 0.63
```

---

## 7. Final Shipping Decision

### VERDICT: SHIP

1. **Hit-rate@3 rose from 66.7% to 83.3% (+16.7 pts)** on the same 12
   questions with the same index: 2 of the 4 genuine retrieval failures fixed,
   none regressed.
2. **Rank-1 accuracy on hits rose from 4/8 to 6/10**, so the gold chunk sits at
   the front of the context window more often.
3. **No measurable latency cost** in the authoritative run (+0.46 ms, inside
   noise).
4. The gain is smaller than the first report claimed in one respect and larger
   in another: two of the original four "failures" were label errors, not
   retrieval errors, while a mislabelled hit (GS-06) was hiding a real fix.
5. Generation quality was not measured here and is not part of this decision.

### What stays broken

GS-04 and GS-05, both in HC-002, both phrased conversationally against a
numbered-step or table-row chunk. GS-04 moved from #9 to #4, so a larger k or
a table-aware boost would likely catch it; GS-05 did not move. These are the
inputs for the next single change, not a reason to withhold this one.
