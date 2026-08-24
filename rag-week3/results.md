# Week 3 – Module 2: Retrieval & RAG — Results

CloudDesk help centre RAG application: 6 mock articles, 3 chunking
strategies, 12 indexed configurations, 8 known-answer questions,
6 unsupported questions and 4 ambiguous questions.

All numbers on this page were produced by the scripts in
[experiments/](experiments/) and are stored as JSON in
[evaluation/](evaluation/). Nothing here is estimated.

| Script | Output |
|---|---|
| `experiments/evaluate_retrieval.py` | `evaluation/retrieval_results.json` |
| `experiments/evaluate_filtering.py` | `evaluation/filtering_results.json` |
| `experiments/calibrate_threshold.py` | `evaluation/threshold_results.json` |
| `experiments/evaluate_generation.py` | `evaluation/generation_results.json` |

---

## 1. Corpus and metadata

Six mock help-centre articles, each with YAML frontmatter. Four of the
six contain a 6-row troubleshooting table.

| Article | Product area | Last updated | Troubleshooting table |
|---|---|---|---|
| HC-001 Password Reset | Authentication | 2026-08-01 | yes |
| HC-002 Account Locked | Authentication | 2026-08-05 | yes |
| HC-003 Billing & Payments | Billing | 2026-08-03 | yes |
| HC-004 Ticket Search & Filtering | Ticket Management | 2026-08-08 | no |
| HC-005 Notification Settings | Notifications | 2026-08-10 | yes |
| HC-006 File Uploads & Storage | Storage | 2026-08-12 | yes |

Every chunk carries `source_file`, `article_id`, `product_area` and
`last_updated`, plus `chunk_id`, `strategy`, `section` and `is_table`
added during chunking.

---

## 2. Chunking strategies

Three strategies, all run over the same 6 documents.

| Strategy | How it splits |
|---|---|
| `recursive` | `RecursiveCharacterTextSplitter` on `\n## `, `\n### `, `\n\n`, `\n`, ` ` |
| `fixed` | `CharacterTextSplitter` on `\n`, fixed character budget |
| `markdown` | Splits on `## ` sections, isolates tables, chunks tables **by row while repeating the header row**, and prefixes every chunk with `Article Title - Section` |

The third strategy was added after the first evaluation round exposed a
structural problem in the other two.

### The table-splitting problem

At `chunk_size=300, overlap=50`:

| Strategy | Chunks | Avg size | Max size | **Table data rows stranded without a header row** |
|---|---|---|---|---|
| recursive | 103 | 191 | 295 | **20** |
| fixed | 82 | 253 | 300 | **20** |
| markdown | 123 | 216 | 298 | **0** |

The four troubleshooting tables hold 24 data rows between them. Both
character-based strategies strand 20 of those 24 rows in chunks that no
longer contain `| Problem | Possible Cause | Solution |`.

A stranded row retrieved on its own looks like this:

```
| Notifications stopped suddenly | Organization settings changed | Contact the CloudDesk administrator |
```

Three bare values with no column names. The generator cannot tell which
column is the cause and which is the fix. The `markdown` strategy
repeats the header row in every table chunk, so a retrieved row is
always interpretable:

```
Notification Settings and Troubleshooting - Notification Troubleshooting
| Problem | Possible Cause | Solution |
|---|---|---|
| Notification sent to wrong email | Incorrect account email | Verify the email address associated with the account |
| Notifications stopped suddenly | Organization settings changed | Contact the CloudDesk administrator |
```

Reproduce with `python src/chunking.py`.

---

## 3. Retrieval evaluation

### 3.1 Why article-level Hit@5 was useless

The first version of the evaluation scored a question as a hit when the
**expected article** appeared in the top K. With only 6 articles, that
metric saturates immediately:

> Recursive 100% Hit@3 / 100% Hit@5 · Fixed 100% Hit@3 / 100% Hit@5

Both strategies scored perfectly, which provided no basis at all for
choosing between them. The metric was measuring "did we land in a
6-way bucket", not "did we retrieve the answer".

**`article_hit@3` is 100% in 11 of the 12 configurations tested.** It is
kept in the results below only to show that it does not discriminate.

### 3.2 The metrics actually used

| Metric | Definition |
|---|---|
| `article_hit@k` | expected `article_id` appears in the top k (the old metric) |
| `chunk_hit@k` | a top-k chunk **literally contains the answer text** and comes from the expected article |
| `table_ok@k` | table questions only: the answer row **and** the table header row are in the same retrieved chunk |
| `MRR` | 1 / rank of the first chunk containing the answer |

Each question in `evaluation/questions.json` carries an `answer_keys`
list — the exact strings that must appear in a chunk for it to count.
Scoring is literal substring matching after whitespace/case
normalisation, so it is objective and reproducible.

### 3.3 Full sweep: 3 strategies × 4 size/overlap settings × Top-3/Top-5

8 questions, 4 of them table-based.

| config (strategy/size/overlap) | chunks | art@3 | art@5 | chk@3 | chk@5 | tbl@3 | tbl@5 | MRR |
|---|---|---|---|---|---|---|---|---|
| recursive/200/0 | 165 | 88% | 100% | 62% | 62% | 0% | 0% | 0.542 |
| recursive/300/50 | 103 | 100% | 100% | 50% | 50% | 0% | 0% | 0.438 |
| recursive/500/100 | 67 | 100% | 100% | 50% | 62% | 0% | 25% | 0.442 |
| recursive/800/150 | 33 | 100% | 100% | 62% | 75% | 25% | 50% | 0.442 |
| fixed/200/0 | 123 | 100% | 100% | 50% | 62% | 0% | 0% | 0.365 |
| fixed/300/50 | 82 | 100% | 100% | 50% | 62% | 0% | 0% | 0.448 |
| fixed/500/100 | 51 | 100% | 100% | 75% | 75% | 25% | 25% | 0.521 |
| fixed/800/150 | 32 | 100% | 100% | 50% | 75% | 0% | 25% | 0.473 |
| markdown/200/0 | 190 | 100% | 100% | 50% | 50% | 50% | 50% | 0.354 |
| **markdown/300/50** | **123** | **100%** | **100%** | **75%** | **88%** | **50%** | **75%** | **0.650** |
| markdown/500/100 | 65 | 100% | 100% | 62% | 75% | 25% | 50% | 0.483 |
| markdown/800/150 | 54 | 100% | 100% | 62% | 62% | 25% | 25% | 0.458 |

Observations:

- **Article-level Hit is saturated; chunk-level Hit spans 50–88%.**
  The old metric could not see a 38-point spread in real quality.
- **`markdown/300/50` wins on every discriminating metric** —
  `chunk_hit@5` 88%, `table_ok@5` 75%, MRR 0.650 (the next best MRR is
  fixed/500/100 at 0.521).
- **Table questions are where the strategies separate.**
  `recursive/300/50` and `fixed/300/50` score **0%** on `table_ok` at
  both K values: they never once retrieved a table row together with its
  header. `markdown/300/50` scores 75%.
- **Bigger chunks partially rescue the naive strategies.** `recursive`
  climbs from 0% to 50% `table_ok@5` as chunk size grows 300 → 800,
  because an 800-character chunk is large enough to swallow a whole
  table by accident. That is luck, not structure — and it costs
  precision (`chunk_hit@3` never exceeds 62% for recursive).
- **Larger chunks hurt the markdown strategy** (88% → 62% `chunk_hit@5`
  from 300 → 800). Once tables are already protected, bigger chunks only
  dilute the embedding with unrelated prose.
- **Zero overlap is the worst setting for `markdown`** (200/0 scores
  0.354 MRR, the lowest of all 12 configs).

### 3.4 Top-3 vs Top-5

For the winning configuration:

| K | article_hit | chunk_hit | table_ok |
|---|---|---|---|
| Top-3 | 100% | 75% | 50% |
| Top-5 | 100% | 88% | 75% |

Going from Top-3 to Top-5 buys **+13 points of `chunk_hit` and +25
points of `table_ok`** at the cost of two extra chunks of context.
Table answers benefit most: table chunks tend to rank 3rd–5th because
the terse pipe-delimited row text embeds less strongly than the
surrounding prose.

**Top-5 is used in the application.**

---

## 4. Metadata filtering

`experiments/evaluate_filtering.py`, run against the `markdown` index at
Top-5. Three comparisons: correct filter, deliberately wrong filter, and
ambiguous queries.

### 4.1 Correct filter vs no filter

| | unfiltered | filtered (`product_area`) |
|---|---|---|
| `chunk_hit@3` | 75% | **88%** |
| `chunk_hit@5` | 88% | 88% |
| MRR | 0.650 | **0.667** |

Filtering **moves the right chunk up the ranking** rather than finding
chunks that were previously unreachable: `chunk_hit@3` gains 13 points
while `chunk_hit@5` is unchanged. Two off-topic chunks were evicted from
the top-5 across the 8 questions.

Q4 is the clearest case — "My card expired and the CloudDesk payment
failed. What should I do?":

| | unfiltered top-5 | filtered to `Billing` |
|---|---|---|
| chunks from the wrong product area | 2 of 5 (HC-006 Storage, HC-002 Authentication) | 0 of 5 |
| chunk ids changed | — | 4 of 5 |

Unfiltered, the query pulled in *file upload* troubleshooting and
*account lock* administrator guidance, because "failed", "expired" and
"contact the administrator" are shared vocabulary across all four
troubleshooting tables. The filter removed both.

### 4.2 Wrong filter — proof the filter is really applied

Every question was also run with a deliberately incorrect
`product_area`:

> **`chunk_hit@5` with the wrong filter: 0% (0 of 8).**

Retrieval still returned 5 chunks each time, but never the answer. This
confirms the `where` clause is applied at query time and not silently
ignored.

### 4.3 Ambiguous queries

Queries with no subject cannot be resolved by the embedding alone.
Metadata supplies the scope the wording is missing:

| Query | Unfiltered top-3 articles | Filter | Filtered top-3 articles |
|---|---|---|---|
| "How do I fix it?" | HC-001, HC-001, HC-006 | `product_area=Billing` | HC-003, HC-003, HC-003 |
| "How long do I have to wait?" | HC-006, HC-002, HC-002 | `article_id=HC-002` | HC-002, HC-002, HC-002 |
| "Why is it not working?" | HC-005 ×3 | `product_area=Notifications` | HC-005 ×3 (no change) |
| "What are the limits?" | HC-006 ×3 | `product_area=Storage` | HC-006 ×3 (no change) |

The first two show filtering rescuing a query that landed in the wrong
article entirely. The last two show it is not always needed — the
embedding already picked the right article, and the filter is a no-op.

Note "How long do I have to wait?" is genuinely ambiguous between the
**30-minute** reset-link validity (HC-001) and the **15-minute** account
lock (HC-002); unfiltered, its top hit was HC-006 (file uploads), which
is neither.

---

## 5. Refusal threshold calibration

`experiments/calibrate_threshold.py` measures the top-1 cosine distance
for all 8 supported and all 6 unsupported questions, then sweeps every
candidate threshold.

### 5.1 The original threshold was wrong

The initial implementation used `RELEVANCE_THRESHOLD = 0.5`. Measured:

| threshold | supported answered | wrongly refused | wrongly answered | accuracy |
|---|---|---|---|---|
| 0.50 | 7/8 | 1 | 2 | 78.6% |
| 0.70 | 7/8 | 1 | 2 | 78.6% |
| **0.75** | **8/8** | **0** | **2** | **85.7%** |
| 0.80 | 8/8 | 0 | 3 | 78.6% |
| 0.95 | 8/8 | 0 | 4 | 71.4% |

At 0.50, Q4 (top-1 distance **0.7340**) was refused even though its
answer is in the corpus. **The threshold was raised to 0.75.**

### 5.2 The near-miss traps: why no threshold can work

Two of the unsupported questions were written deliberately so that
retrieval *succeeds* while the answer does not exist:

| Question | top-1 distance | Why unanswerable |
|---|---|---|
| U3 "What is the maximum file size limit … in megabytes?" | **0.2729** | HC-006 discusses the size limit at length but never states a number |
| U4 "How many failed login attempts lock an account?" | **0.2902** | HC-002 says "multiple"/"several", never a threshold |

Compare against the supported questions:

| | range of top-1 distances |
|---|---|
| Supported (Q1–Q8) | 0.2739 – 0.7340 |
| U3, U4 (traps) | **0.2729 – 0.2902** |

**U3 scores closer than every single supported question.** A retrieval
distance gate is structurally incapable of separating "the topic is in
the corpus" from "the specific fact is in the corpus". This is the main
finding of the refusal work: *distance measures topical similarity, not
answerability.* Anything that survives the gate must be caught after
generation.

---

## 6. Grounded answers, citations and refusals

### 6.1 Three gates

```
query ─► retrieve top-5
          │
          ├─ Gate 1  distance gate      top-1 distance > 0.75        ─► refuse
          ├─ Gate 2  model gate         model emits NOT_IN_SOURCES   ─► refuse
          └─ Gate 3  grounding gate     no chunk supports the answer ─► refuse
                                        otherwise ─► answer + citations
```

### 6.2 Citations are verified, not claimed

`Qwen2.5-0.5B-Instruct` proved unable to answer *and* cite reliably. Two
failure modes were observed while building this:

- Putting the literal refusal sentence in the system prompt **primed the
  model to emit it even when the answer was present**. Q1 ("How long is
  a reset link valid?") was refused despite source [1] containing
  "A password reset link is valid for 30 minutes." Replacing the
  sentence with a `NOT_IN_SOURCES` sentinel fixed it.
- Asked to answer *and* cite, the model often emitted only `[1]` with no
  answer text.

So citations are **not** taken from the model's output. `src/grounding.py`
scores every retrieved chunk against the generated answer:

- **coverage** — fraction of the answer's content words present in the
  chunk (must be ≥ 0.55)
- **unsupported numbers** — any number in the answer that does not
  appear in the chunk (must be empty)

Only chunks that pass both are cited, and the citation markers are
renumbered to match. A citation therefore *cannot* point at a chunk that
does not contain the answer. The number check is a cheap, direct
hallucination guard: fabricated limits, prices and durations are exactly
what a small model invents.

### 6.3 Results on the 8 supported questions

| Metric | Result |
|---|---|
| Answered (not refused) | 6/8 (75%) |
| Answer correct | 5/8 (62.5%) |
| **Citation points to the correct article** | **100%** (6/6 answered) |
| **Citation points to a chunk that contains the answer** | **100%** (6/6 answered) |

Example — Q6, a table question:

```
Q: Why is the Upload File button unavailable in CloudDesk?
best distance: 0.3716

A: User lacks required permission. [1] [2]

Citations:
   [1] HC-006 / HC-006-markdown-11 - article_06.md
       (section: File Upload Troubleshooting, updated 2026-08-12, coverage 1.0)
   [2] HC-006 / HC-006-markdown-20 - article_06.md
       (section: File Permissions, updated 2026-08-12, coverage 0.75)
```

The answer comes from a table row, and the cited chunk contains that row
*with its header* — only possible because of the markdown strategy.

Reproduce with `python main.py demo`.

### 6.4 Refusals: 6/6

| ID | Question | Distance | Caught by |
|---|---|---|---|
| U1 | Refund policy for annual subscriptions | 0.9194 | Gate 1 distance |
| U2 | Does CloudDesk integrate with Salesforce? | 0.7744 | Gate 1 distance |
| U3 | Maximum file size limit in megabytes | 0.2729 | **Gate 2 model** |
| U4 | How many failed login attempts lock an account | 0.2902 | **Gate 2 model** |
| U5 | Enterprise plan cost per user | 1.0468 | Gate 1 distance |
| U6 | What is the capital of France? | 1.7898 | Gate 1 distance |

**Refusal rate: 100% (6/6). No hallucinated answer was produced.**

The split matters: the distance gate caught 4, and the two it
structurally could not catch were caught by the grounding layer. Neither
mechanism alone would have scored 100% — the distance gate alone scores
4/6.

All four ambiguous questions were also refused (distances 1.12–1.41),
which is the desired behaviour: a subject-less question should be
refused rather than answered from an arbitrary article.

---

## 7. Retrieval failures

39 failures were recorded across the 12 configurations. Distribution:

| Question | Failures (of 12 configs) | Failure mode |
|---|---|---|
| **Q3** reset link expired | **12** | answer chunk never retrieved |
| **Q4** card expired | 10 | answer chunk never retrieved |
| Q5 notifications stopped | 7 | mixed |
| Q6 upload button unavailable | 6 | mixed |
| Q7 password requirements | 3 | answer chunk never retrieved |
| Q1 reset link validity | 1 | answer chunk never retrieved |

By type: 28 table-answer misses, 7 `table_header_lost` (row retrieved
but header stranded), 4 standard misses.

**All four table questions dominate the failure list.** Table content is
the hardest thing in this corpus to retrieve.

### Failure case study: Q3 fails in all 12 configurations

*"What should I do if my CloudDesk password reset link has expired?"*

The answer is in `HC-001-markdown-11`:

```
Password Reset - Troubleshooting
| Problem | Possible Cause | Solution |
|---|---|---|
| Reset link has expired | Link is older than 30 minutes | Request a new password reset link |
```

Retrieved top-5 (markdown/300/50):

| rank | chunk | distance | section |
|---|---|---|---|
| 1 | HC-001-markdown-2 | 0.3611 | How to Reset Your Password |
| 2 | HC-001-markdown-3 | 0.4115 | How to Reset Your Password |
| 3 | HC-001-markdown-14 | 0.4741 | Security Information |
| 4 | HC-001-markdown-4 | 0.5074 | How to Reset Your Password |
| 5 | HC-001-markdown-1 | 0.5503 | Overview |
| … | | | |
| **21** | **HC-001-markdown-11** | **0.8217** | **Troubleshooting** ← the answer |

`article_hit@5` reports a **perfect hit** here — every one of the top 5
is from HC-001. The article is right and the chunk is wrong, which is
exactly the blindness the old metric had.

Root cause: the question is phrased as a *procedure* request ("What
should I do if…"), which embeds close to the procedural chunks. HC-001
contains five "How to Reset Your Password" chunks that crowd the top
ranks. The table row is terse pipe-delimited text with no verbs, so its
embedding is weak for a conversational query.

Two things fix it, both verified:

| Intervention | Gold chunk rank | Distance |
|---|---|---|
| none | 21 of 123 | 0.8217 |
| filter `is_table=True` | **3** | 0.8217 |
| rephrase to "reset link has expired what is the solution" | **1** | 0.5853 |

So the failure is a **query/content phrasing mismatch**, not a chunking
defect — the markdown strategy did produce a clean, header-carrying
chunk; the retriever simply did not rank it. The `is_table` metadata
flag is the practical lever, and routing troubleshooting-style questions
through it is the obvious next step (see §9).

Note the pipeline **refused Q3 rather than answering it wrongly**, which
is the correct behaviour given the answer was not in context.

### Q4 is a different failure

For Q4 the gold chunk *was* retrieved (`HC-003-markdown-13`), but the
generator still emitted `NOT_IN_SOURCES`. That is a **generation**
failure, not a retrieval failure, and it is attributable to the 0.5B
model — the retrieval layer did its job. Distinguishing the two was only
possible because retrieval and generation are scored separately.

---

## 8. Final chunking decision

**`markdown` (table-aware), `chunk_size=300`, `chunk_overlap=50`, Top-5.**

Evidence:

1. **Best on every discriminating metric** — `chunk_hit@5` 88%,
   `table_ok@5` 75%, MRR 0.650. The nearest competitor on MRR is
   `fixed/500/100` at 0.521.
2. **Only 1 retrieval failure of 8 questions**, against 3–5 for every
   character-based configuration.
3. **Structurally eliminates the table defect** — 0 stranded table rows
   vs 20 for both alternatives. `recursive/300/50` and `fixed/300/50`
   score **0%** on `table_ok`; they cannot answer a troubleshooting-table
   question with an interpretable citation at all.
4. **Every chunk carries its own provenance** (`Article - Section`
   prefix), which improves the retrieved context and makes citations
   readable without a second lookup.
5. **The gain is not from luck.** `recursive/800/150` reaches 50%
   `table_ok@5` only because an 800-character window happens to swallow a
   whole table, and it pays for that with worse precision at Top-3.

Cost: 123 chunks vs 82 for `fixed` — 50% more vectors for a corpus this
size, which is negligible here and would need re-checking at scale.

---

## 9. Limitations and next steps

- **Small evaluation set.** 8 questions means one question is worth 12.5
  points; the differences between adjacent configurations are within
  noise. The strategy-level gaps (0% vs 75% `table_ok`) are large enough
  to be real; the size/overlap gaps within a strategy are not.
- **The generator is the weak link.** `Qwen2.5-0.5B-Instruct` refused two
  answerable questions (Q3, Q4) and gave a partial answer to Q5. Answer
  accuracy 62.5% is a generation ceiling, not a retrieval one — retrieval
  put the answer in context for 88% of questions. A larger model is the
  single highest-value change.
- **No query rewriting.** The Q3 analysis shows rephrasing moves the gold
  chunk from rank 21 to rank 1. An LLM query-rewriting step, or routing
  troubleshooting questions through the `is_table=True` filter, should
  recover it.
- **No hybrid retrieval.** Table rows are keyword-dense and
  embedding-poor — exactly the case BM25 handles well. Hybrid dense+sparse
  retrieval is the natural fix for the remaining table failures.
- **Metadata filters are supplied manually.** `--area` is passed on the
  command line. Inferring the product area from the query would make the
  filtering benefit automatic.
- **`last_updated` is stored but never filtered on.** Chroma compares
  metadata strings by equality; range queries over dates would need the
  date stored as an integer.
