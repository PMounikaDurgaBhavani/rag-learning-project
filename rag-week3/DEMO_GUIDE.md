# CloudDesk RAG — Week 3 & Week 4 Demo Guide

A single reference for presenting both weeks: what was built, which concepts and
models were used and why, what changed in Week 4, and a timed runbook for the
live demo.

Every number here is traceable to a file in [evaluation/](evaluation/).

---

## 1. What the system is

Six mock CloudDesk help-centre articles (`HC-001`–`HC-006`) covering
Authentication, Billing, Ticket Management, Notifications and Storage. Four of
the six carry a six-row troubleshooting table — and those tables are the hardest
content in the corpus to retrieve. That single fact drove nearly every design
decision across both weeks.

| Article | Product area | Last updated | Troubleshooting table |
|---|---|---|---|
| HC-001 Password Reset | Authentication | 2026-08-01 | yes |
| HC-002 Account Locked | Authentication | 2026-08-05 | yes |
| HC-003 Billing & Payments | Billing | 2026-08-03 | yes |
| HC-004 Ticket Search & Filtering | Ticket Management | 2026-08-08 | no |
| HC-005 Notification Settings | Notifications | 2026-08-10 | yes |
| HC-006 File Uploads & Storage | Storage | 2026-08-12 | yes |

### Pipeline

```
load (YAML frontmatter → metadata)
  → chunk (3 strategies; table-aware default)
    → embed (all-MiniLM-L6-v2, 384-dim)
      → index (ChromaDB, one collection per strategy)
      → index (BM25Okapi, in-memory)                    ← Week 4
        → retrieve Top-K (+ product_area / article_id / is_table filters)
        → fuse dense + BM25 ranks with RRF (k=60)       ← Week 4
          → 3 refusal gates (distance · model · grounding)
            → answer (Qwen2.5-0.5B-Instruct) + verified citations
```

### Metadata on every chunk

Required by the brief: `source_file`, `article_id`, `product_area`,
`last_updated`. Added on top: `chunk_id`, `strategy`, `section`, `is_table`.
The last one is what lets a troubleshooting-style question be routed straight at
table content.

---

## 2. Models and libraries — and why each one

Everything runs locally on CPU. No API key, no network call at query time. That
was deliberate: both weeks are about repeatable measurement, and a hosted model
that changes underneath you makes before/after comparison meaningless.

| Component | Choice | Why this one |
|---|---|---|
| **Embedding model** | `all-MiniLM-L6-v2` | 384 dimensions, ~22M params, comfortable on CPU. Strong sentence-similarity quality per millisecond, which matters when the evaluation sweeps 12 index configurations. Frozen across both weeks so the Week 4 comparison has exactly one moving part. |
| **Vector store** | ChromaDB (persistent) | Local, zero-config, and supports a metadata `where` clause at query time — the whole basis of the metadata-filtering deliverable. One collection per chunking strategy so strategies are compared over identical documents. |
| **Generator** | `Qwen2.5-0.5B-Instruct` | Small enough for laptop CPU with greedy decoding (`do_sample=False`), so answers are reproducible run to run. Its weakness — not reliable enough to cite its own sources — is exactly what forced the verified-citation design. |
| **Splitters** | `langchain-text-splitters` | `RecursiveCharacterTextSplitter` and `CharacterTextSplitter` supply the two baseline strategies. The third is hand-written because no off-the-shelf splitter keeps a markdown table header attached to its rows. |
| **Lexical retriever** *(Week 4)* | `rank_bm25` · `BM25Okapi` | Classical sparse ranking over a custom tokenizer that preserves identifiers — the regex `[A-Za-z0-9_\-\.\:]+` keeps `HC-003` intact instead of shredding it into `hc` and `003`. That is the entire reason it beats dense search on error codes and article IDs. |
| **Cross-encoder** *(explored, excluded)* | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Implemented in [src/reranker.py](src/reranker.py) and benchmarked, but deliberately kept **out** of the Week 4 before/after run — the brief allows exactly one retrieval change. Available behind `--rerank` as evidence the alternative was evaluated, not ignored. |

**Why no bigger LLM.** Answer accuracy is capped at 62.5% by the 0.5B generator,
and the write-up says so. It was not swapped out of discipline: retrieval and
generation are scored separately, so a generation ceiling never gets mistaken
for a retrieval problem. Swapping the LLM is named as the highest-value next
change.

---

## 3. Week 3 — build it, then prove it

### Concept 1 · Three chunking strategies over identical documents

| Strategy | How it splits | Chunks | Table rows stranded |
|---|---|---|---|
| `recursive` | Recursive splitter on markdown separators | 103 | **20** |
| `fixed` | Fixed character budget, newline separator | 82 | **20** |
| **`markdown`** | Section-aware; splits tables **by row and repeats the header row**; prefixes every chunk with `Article – Section` | 123 | **0** |

The four troubleshooting tables hold 24 data rows between them. Both
character-based strategies leave 20 of those rows in a chunk that no longer
contains `| Problem | Possible Cause | Solution |`. A stranded row retrieved
alone is three bare values with no column names — the generator cannot tell
which one is the cause and which is the fix.

Reproduce: `python src/chunking.py`

### Concept 2 · A retrieval metric that actually discriminates

The first evaluation scored a hit when the expected *article* appeared in the
top K. With six articles that saturates instantly — **100% in 11 of 12
configurations**, useless for choosing anything. It was replaced with:

- **`chunk_hit@k`** — a top-k chunk literally contains the answer string *and*
  comes from the expected article
- **`table_ok@k`** — table questions only: the answer row *and* its header row
  are in the same chunk
- **`MRR`** — 1 / rank of the first chunk containing the answer
- **`article_hit@k`** — kept only to show it does not discriminate

Scoring is literal substring matching against an `answer_keys` list after case
and whitespace normalisation — objective, no LLM judge, reproducible.

#### The sweep: 3 strategies × 4 size/overlap settings × Top-3/Top-5

| Config | Chunks | art@5 | chk@3 | chk@5 | tbl@5 | MRR |
|---|---|---|---|---|---|---|
| recursive/300/50 | 103 | 100% | 50% | 50% | 0% | 0.438 |
| recursive/800/150 | 33 | 100% | 62% | 75% | 50% | 0.442 |
| fixed/300/50 | 82 | 100% | 50% | 62% | 0% | 0.448 |
| fixed/500/100 | 51 | 100% | 75% | 75% | 25% | 0.521 |
| **markdown/300/50** | **123** | **100%** | **75%** | **88%** | **75%** | **0.650** |
| markdown/500/100 | 65 | 100% | 62% | 75% | 50% | 0.483 |
| markdown/800/150 | 54 | 100% | 62% | 62% | 25% | 0.458 |

Three readings worth saying out loud:

1. Article-level hit is flat at 100% while chunk-level hit spans 50–88% — the
   old metric was blind to a 38-point spread.
2. Table questions are where strategies separate: `recursive/300/50` and
   `fixed/300/50` score **0%** on `table_ok` at both K values.
3. `recursive/800/150` only reaches 50% because an 800-character window happens
   to swallow a whole table — luck, paid for with worse precision at Top-3.

#### Top-3 vs Top-5 on the winning config

Top-5 buys **+13 points of `chunk_hit` and +25 points of `table_ok`** for two
extra chunks of context. Table chunks tend to rank 3rd–5th because terse
pipe-delimited rows embed weakly against surrounding prose. **Top-5 is what the
application uses.**

### Concept 3 · Metadata filtering, and proof it is really applied

| Run | chunk_hit@3 | chunk_hit@5 | MRR |
|---|---|---|---|
| Unfiltered | 75% | 88% | 0.650 |
| Filtered on correct `product_area` | **88%** | 88% | **0.667** |
| Filtered on a deliberately **wrong** area | **0%** | **0%** | 0.000 |

Filtering *promotes* the right chunk rather than finding unreachable ones —
hit@3 gains 13 points while hit@5 is unchanged. The wrong-filter run is the
important one: retrieval still returns five chunks every time but never the
answer, which proves the `where` clause is enforced at query time and not
silently dropped.

On the ambiguous query *"How do I fix it?"*, unfiltered retrieval returns
HC-001, HC-001, HC-006; filtered to `Billing` it returns HC-003 three times over.

### Concept 4 · Refusal is three gates, because one is provably not enough

| Gate | Mechanism |
|---|---|
| **1 · Distance** | Top-1 cosine distance > 0.75 → refuse. *Calibrated, not guessed*: at the original 0.50 a genuinely answerable question (distance 0.7340) was wrongly refused. A full sweep put the accuracy peak at 0.75. |
| **2 · Model** | The model emits the sentinel `NOT_IN_SOURCES`. A sentinel, **not** the refusal sentence — putting the literal refusal text in the prompt primed the 0.5B model to emit it even when the answer was present. |
| **3 · Grounding** | No retrieved chunk actually supports the generated text → refuse. Scored on content-word coverage (≥ 0.55) plus a check for numbers asserted in the answer but absent from the chunk. |

**The finding worth leading with.** Two unsupported questions were written as
deliberate traps — the corpus discusses the topic at length but never states the
fact. *"What is the maximum file size limit in megabytes?"* retrieves at
distance **0.2729**. Every supported question sits between 0.2739 and 0.7340.
**The trap retrieves closer than every answerable question.**

So a distance threshold is structurally incapable of separating "the topic is in
the corpus" from "the fact is in the corpus". Gate 1 alone catches 4 of 6.
Gates 2 and 3 catch the other two. **Refusal rate: 6/6, zero hallucinated
answers.**

### Concept 5 · Citations are verified, not claimed

Citation markers are never taken from the model's output. Every retrieved chunk
is scored against the generated answer in [src/grounding.py](src/grounding.py),
and only chunks passing both checks are cited, with markers renumbered to match.
A citation therefore *cannot* point at a chunk that does not contain the answer
— measured at **100% correct article and 100% correct chunk** across the six
answered questions. The number check doubles as a cheap hallucination guard:
fabricated limits, prices and durations are exactly what a small model invents.

### Concept 6 · Failures recorded, not hidden

39 retrieval failures across the 12 configurations, dominated by the four table
questions: 28 table-answer misses, 7 cases where the row was retrieved but the
header was stranded, 4 ordinary misses.

Case study — Q3, *"What should I do if my reset link has expired?"*, fails in
all 12 configurations:

| Intervention | Gold chunk rank | Distance |
|---|---|---|
| None | 21 of 123 | 0.8217 |
| Filter `is_table=True` | **3** | 0.8217 |
| Rephrase to "reset link has expired what is the solution" | **1** | 0.5853 |

All five top-ranked chunks come from HC-001, so `article_hit@5` reports a perfect
hit while the answer sits at rank 21 — exactly the blindness the old metric had.
Root cause: the question is phrased as a procedure request and embeds close to
the five "How to Reset Your Password" chunks, while the table row is terse and
verbless. This is a **query/content phrasing mismatch, not a chunking defect** —
and it is what sets up Week 4.

### Week 3 decision

**`markdown` chunking, `chunk_size=300`, `chunk_overlap=50`, Top-5.** Best on
every discriminating metric, structurally eliminates the table defect, and the
gain is not luck. Cost: 123 chunks versus 82 for `fixed` — 50% more vectors,
negligible at this size, worth rechecking at scale.

---

## 4. Week 4 — one change, measured honestly

Week 4 adds no features to the answer path. It asks a narrower, harder question:
where does the Week 3 retriever fail, and does one specific fix move the number?

### Step 1 · A 12-question golden set, written before any retrieval ran

Each entry in [evaluation/golden_set.jsonl](evaluation/golden_set.jsonl) names
the question, the exact `expected_chunk_id`, and the answer key. **Six of the
twelve contain exact identifiers** (`HC-001`–`HC-006`) against a required
minimum of four. Declaring the expected chunk ID first is what makes the
exercise honest — there is no way to move the goalposts after seeing results.

### Step 2 · Baseline, then classify every miss

All 12 questions through the Week 3 dense retriever at Top-3.

- **Baseline hit-rate@3: 8/12 = 66.7%**
- **Baseline p50 latency: 17.97 ms**
- **Failure tally: R = 4 · G = 0 · Not-In-Corpus = 0**

| ID | Expected chunk | Evidence — what came back instead |
|---|---|---|
| GS-01 | `HC-001-markdown-5` | Retrieved `-15, -6, -18`. Dense search preferred general FAQ and password-complexity prose over the chunk stating the 30-minute validity. |
| GS-05 | `HC-002-markdown-10` | Retrieved `-7, -4, -3`. Matched high-level lock-explanation paragraphs instead of the row `\| Incorrect password entered \| Wait 15 minutes \|`. |
| GS-09 | `HC-005-markdown-12` | Retrieved `-22, -16, -10`. Favoured browser-notification troubleshooting over the row `\| Notifications stopped suddenly \| Organization settings changed \|`. |
| GS-12 | `HC-006-markdown-9` | Retrieved `-24, -11, -20`. Returned general upload-failure prose instead of the row `\| Upload button unavailable \| User lacks required permission \|`. |

Three of the four misses are troubleshooting-table rows, and half the set
contains an exact identifier the embedding treats as ordinary text. That points
at one fix, not two.

### Step 3 · The one change: BM25 + Reciprocal Rank Fusion

Dense search is asked for 15 candidates. BM25 is asked for 15 candidates over
the same chunks. The two ranked lists are fused **by position, not by score** —
which is why no normalisation is needed between a cosine distance and a BM25
score:

```
RRF_score(d) = 1 / (60 + rank_dense(d)) + 1 / (60 + rank_bm25(d))
```

The choice was between BM25 + RRF and cross-encoder reranking. Reranking helps
when the right chunk is already in the candidate pool but ranked low; BM25 helps
when exact tokens are missed entirely. The failure evidence — identifiers and
terse table rows — pointed at the second. The cross-encoder was still built and
benchmarked, but kept out of the comparison so only one variable moved.

#### The entire retrieval diff

```diff
- def retrieve(query, top_k=3):
-     return retrieve_chunks(query, top_k=top_k, strategy="markdown")
+ def retrieve(query, top_k=3):
+     return retrieve_hybrid(query, top_k=top_k, strategy="markdown", rerank=False)

  unchanged: embedding model, chunking strategy, chunk size, overlap,
             prompt template, LLM, top_k, golden set
```

### Step 4 · Same 12 questions, per-question result

| ID | Expected chunk | Before | After | Status |
|---|---|---|---|---|
| GS-01 | `HC-001-markdown-5` | miss | #2 | **FIXED** |
| GS-02 | `HC-001-markdown-6` | #1 | #2 | held |
| GS-03 | `HC-001-markdown-11` | #1 | #1 | held |
| GS-04 | `HC-002-markdown-5` | #2 | #2 | held |
| GS-05 | `HC-002-markdown-10` | miss | miss | unfixed |
| GS-06 | `HC-003-markdown-9` | #2 | **#1** | promoted |
| GS-07 | `HC-003-markdown-13` | #1 | #1 | held |
| GS-08 | `HC-004-markdown-8` | #3 | **#1** | promoted |
| GS-09 | `HC-005-markdown-12` | miss | miss | unfixed |
| GS-10 | `HC-005-markdown-23` | #3 | **#1** | promoted |
| GS-11 | `HC-006-markdown-3` | #1 | #1 | held |
| GS-12 | `HC-006-markdown-9` | miss | miss | unfixed |

**1 of 4 R-failures fixed, 3 unfixed, zero regressions.** The quieter result
matters more than the headline: three questions were *promoted to rank 1*, so
rank-1 accuracy on hits went from 4/8 to 6/9 — 50% → 66.7%. The gold chunk now
sits at the front of the LLM's context window more often, which is where a small
generator is most likely to actually use it.

### Step 5 · The shipping decision

| Metric | Before | After | Delta |
|---|---|---|---|
| Hit-rate@3 | 66.7% (8/12) | 75.0% (9/12) | **+8.3 pts** |
| Rank-1 accuracy on hits | 50.0% (4/8) | 66.7% (6/9) | **+16.7 pts** |
| p50 latency | 17.97 ms | 11.35 ms | −6.62 ms *(see caveat)* |
| Regressions | — | 0 | — |

**Verdict: SHIP.** Recall up, rank-1 precision up, no regressions, latency well
inside budget.

> **Say this before you are asked.** The improved retriever measuring *faster*
> than the baseline is counter-intuitive — hybrid retrieval does strictly more
> work, since it runs the same dense query for 15 candidates and then a BM25
> pass on top. The most likely explanation is warm-up: the baseline loop ran
> first and paid the one-off cost of loading the embedding model and opening the
> Chroma collection. Naming this yourself is stronger than being caught by it.
> The honest claim is **"latency stayed inside budget"**, not "hybrid is faster".
> A clean re-measurement would discard warm-up iterations and interleave the two
> retrievers.

### Why the three unfixed questions stayed unfixed

All three are troubleshooting-table rows, and all three fail for the reason
Week 3 already diagnosed on Q3: the question is conversational (*"What should I
do if…"*, *"Why is… unavailable"*) while the target is a verbless
pipe-delimited row. BM25 helps when the question and the row share rare tokens;
it does not help when the shared vocabulary — "failed", "expired", "contact the
administrator" — is common across all four troubleshooting tables. The two
levers already identified: route troubleshooting-style questions through the
`is_table=True` filter, or rewrite the query (which moved Q3 from rank 21 to
rank 1).

---

## 5. Week 3 → Week 4, side by side

| Dimension | Week 3 | Week 4 |
|---|---|---|
| **Question** | Which chunking strategy and Top-K should we use? | Where does retrieval break, and does one fix move it? |
| **Evaluation set** | 8 known-answer + 6 unsupported + 4 ambiguous | 12-question golden set with pre-declared `expected_chunk_id` |
| **Headline metric** | `chunk_hit@5`, `table_ok@5`, MRR | hit-rate@3 with before/after p50 latency |
| **Retrieval** | Dense only — MiniLM over ChromaDB | **new** Dense + BM25Okapi fused with RRF (k = 60) |
| **Failure handling** | Failures counted; one case study by hand | **new** Automated R / G / Not-In-Corpus classification with per-question evidence |
| **Diagnostics** | `--show-retrieved` on the CLI | **new** `inspect` — side-by-side dense vs BM25 vs hybrid vs reranked, plus grounding scores |
| **Query handling** | Query used verbatim | **new** Query rewriting and HyDE as CLI commands (not in the measured comparison) |
| **Ingestion** | Markdown with YAML frontmatter | **new** Universal loader — PDF, DOCX, PPTX, XLSX, CSV, JSON, HTML, images via OCR, sidecar `.meta.json` |
| **Interface** | CLI only | **new** `python main.py ui` — browser studio with upload, re-index and inspection |
| **Held constant** | — | Embedding model, chunking strategy, chunk size, overlap, prompt, LLM, refusal gates, golden set |

> **The single-variable rule.** Rewriting, HyDE, the cross-encoder, the universal
> loader and the web UI all exist in the repo. *None of them touched the measured
> before/after run.* If asked why the extra features are there but not in the
> numbers, that restraint is the correct answer — state it first rather than
> defending it later.

### Reference: retrieval methods benchmarked on the Week 3 8-question set

| Method | hit@1 | hit@3 | hit@5 | MRR | Table grounding |
|---|---|---|---|---|---|
| Dense only (MiniLM) | 50.0% | 75.0% | 87.5% | 0.650 | 75.0% |
| BM25 only (lexical) | 50.0% | 87.5% | 100% | 0.650 | 100% |
| **Hybrid RRF (dense + BM25)** | **87.5%** | 87.5% | 87.5% | **0.875** | 75.0% |
| Hybrid + cross-encoder | 62.5% | 75.0% | 87.5% | 0.713 | 75.0% |

This is the evidence behind choosing RRF over reranking: hybrid RRF nearly
doubles rank-1 accuracy and lifts MRR from 0.650 to 0.875, while the
cross-encoder — a general-purpose MS MARCO model with no exposure to this
corpus's terse table rows — actively hurts. On eight questions each result is
worth 12.5 points, so treat the ordering as directional, not decisive.

---

## 6. Demo runbook (~15 minutes)

Run `python main.py ingest` and one throwaway `ask` **before** anyone is
watching — the first call downloads and loads models, and dead air is the only
thing that can go wrong here.

### 0:00 — Frame it in one sentence

> "Six help-centre articles, a local RAG stack, and two weeks of work. Week 3 was
> about choosing a chunking strategy on evidence instead of intuition. Week 4 was
> about finding where retrieval actually breaks and changing exactly one thing to
> fix it."

### 1:00 — The table problem (the whole reason for strategy three)

```bash
python src/chunking.py
```

Point at the last column: 20 stranded table rows for both character-based
strategies, 0 for `markdown`. Show what a stranded row looks like retrieved on
its own — three bare values, no column names.

> "This is why I wrote a third strategy instead of tuning chunk size. No
> character-based splitter keeps a markdown table header attached to its rows."

### 3:00 — Why the obvious metric was useless

Show the sweep table. Article-level hit is 100% in 11 of 12 configurations while
chunk-level hit spans 50–88%.

> "My first metric said every configuration was perfect. That is how I knew the
> metric was wrong, not the system."

### 5:00 — Grounded answer with a verified citation

```bash
python main.py ask "Why is the Upload File button unavailable in CloudDesk?" --show-retrieved
```

The answer comes from a table row and the cited chunk carries that row *with its
header*. Note that the citation was produced by the grounding scorer, not copied
from the model.

### 7:00 — Metadata filtering, including the negative control

```bash
python main.py search "How do I fix it?" --top-k 3
python main.py search "How do I fix it?" --top-k 3 --area Billing
python main.py search "payment failed" --top-k 5 --area Notifications   # wrong filter
```

> "The wrong-filter run is the one that proves it. Five chunks come back every
> time, but never the answer — so the filter is genuinely enforced at query time."

### 9:00 — Refusal, and the trap that breaks the distance gate

```bash
python main.py ask "What is the capital of France?"
python main.py ask "What is the maximum file size limit for CloudDesk uploads in megabytes?"
```

The first is caught by distance at 1.7898. The second retrieves at **0.2729** —
closer than every answerable question in the set — and is caught by the model and
grounding gates instead.

> "Distance measures whether the topic is in the corpus. It cannot measure
> whether the fact is. That is the single most useful thing I learned in Week 3."

### 11:00 — Week 4: the golden set and the baseline

```bash
cat evaluation/golden_set.jsonl
```

Twelve questions with the expected chunk ID declared before any retrieval ran;
six contain an exact identifier. Baseline: 8/12 = 66.7%, p50 17.97 ms, failure
tally R 4 · G 0 · Not-In-Corpus 0.

### 12:30 — The one change, run live

```bash
python main.py search        "How long is a password reset link valid in CloudDesk?" --top-k 3
python main.py hybrid-search "How long is a password reset link valid in CloudDesk?" --top-k 3
```

This is GS-01: a miss on the baseline, rank 2 with hybrid. The hybrid output
prints `rrf=…` alongside each chunk's `dense` and `bm25` rank, which makes the
fusion visible rather than asserted.

Optional deeper view if there is time:

```bash
python main.py inspect "What should I do if my payment fails in HC-003?"
```

### 14:00 — The numbers and the decision

```bash
python experiments/evaluate_week4_improvement.py
```

Hit-rate@3 66.7% → 75.0%. One R-failure fixed, three unfixed, zero regressions,
three questions promoted to rank 1. **Volunteer the latency caveat here** rather
than waiting for it.

> "Ship it — recall up, rank-1 precision up, no regressions, latency inside
> budget. The three that stayed broken are all table rows phrased
> conversationally, and I already know the two levers that move them."

### 15:00 — Close on what you would do next

Swap the 0.5B generator (answer accuracy is capped at 62.5% by generation, not
retrieval). Route troubleshooting-phrased questions through `is_table=True`.
Infer `product_area` from the query so filtering stops being manual. Store
`last_updated` as an integer so date ranges become filterable.

---

## 7. Questions you should expect

**Why did the hybrid retriever measure faster than dense-only?**
It almost certainly is not. Hybrid does strictly more work. The baseline loop ran
first and absorbed model-load and collection-open costs. The defensible claim is
that latency stayed inside budget; a clean re-measurement would discard warm-up
iterations and interleave the two.

**Why only 1 of 4 failures fixed? Was the change worth it?**
Hit-rate@3 is a blunt instrument at n=12. The fuller picture is +8.3 points of
recall *and* three questions promoted to rank 1, so rank-1 accuracy went
50% → 66.7% with zero regressions. On the Week 3 set, hybrid RRF lifts MRR from
0.650 to 0.875.

**Why BM25 rather than the cross-encoder?**
The evidence pointed there. Reranking helps when the right chunk is in the
candidate pool but ranked low; BM25 helps when exact tokens are missed outright,
and half the golden set carries identifiers like `HC-003`. The cross-encoder was
built and benchmarked anyway — on this corpus it scored *worse* than plain RRF
(MRR 0.713 vs 0.875), because an MS MARCO model has no exposure to terse
pipe-delimited table rows.

**How do you know the metadata filter is actually applied?**
The wrong-filter run. Every question was re-run with a deliberately incorrect
`product_area`: `chunk_hit@5` dropped to 0/8 while retrieval still returned five
chunks each time.

**Can a citation ever point at the wrong chunk?**
Not by construction. Markers are never copied from the model — each retrieved
chunk is scored for content-word coverage (≥ 0.55) and for numbers asserted but
absent, and only passing chunks are cited. Measured at 100% correct chunk and
article.

**Isn't 8 questions (Week 3) or 12 (Week 4) too small?**
Yes, and the write-up says so. One question is worth 12.5 points in Week 3 and
8.3 in Week 4, so adjacent configurations are within noise. The gaps claimed as
real are the large structural ones — 0% versus 75% table grounding — not the
one-point differences.

**Why such a small LLM?**
Reproducibility and local execution. Its unreliability is also what produced the
verified-citation design. Because retrieval and generation are scored
separately, the 62.5% answer accuracy is correctly attributed to generation —
retrieval put the answer in context for 88% of questions.

**What breaks first if the corpus grows to 6,000 articles?**
The in-memory BM25 index, which is rebuilt from source documents per process.
Chroma scales; a full re-tokenisation of the corpus does not. The calibrated
distance threshold would also need re-deriving, since it was fitted to this
corpus's distance distribution.

---

## 8. Fix these before submitting

Three repo issues found while assembling this document. None affect the results
— all three affect whether a reviewer can reproduce them.

1. **`results.md` no longer contains Week 3.** The Week 4 report overwrote it.
   Week 3's write-up is intact in commit `48e93d7` and recoverable with
   `git show 48e93d7:rag-week3/results.md`. Both weeks are graded on
   `results.md`, so restore Week 3 and append Week 4 as a second part — or split
   into `results-week3.md` and `results-week4.md`.
2. **`requirements.txt` is missing every ML dependency.**
   `sentence-transformers`, `transformers`, `torch` and `rank_bm25` are all
   imported but none are listed. A fresh clone cannot run `ingest`. Regenerate
   with `pip freeze` from the working venv.
3. **README still describes Week 3 only.** It documents `ingest / ask / search /
   demo` but not `hybrid-search`, `inspect`, `failure-report`,
   `evaluate-hybrid` or `ui`. A short Week 4 section with the before/after
   numbers makes the repo readable without opening a source file.

Also worth re-running: latency, with warm-up iterations discarded and the two
retrievers interleaved. If hybrid turns out to be slower — as it should be — the
shipping decision does not change, and the report becomes considerably more
credible for it.
