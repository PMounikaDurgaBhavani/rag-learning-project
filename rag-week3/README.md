# CloudDesk Help Centre RAG — Week 3, Module 2

A retrieval-augmented generation application over 6 mock help-centre
articles, built to **measure** retrieval quality rather than just
produce answers.

Full evaluation write-up: **[results.md](results.md)**

---

## Headline results

| | |
|---|---|
| Chosen configuration | `markdown` chunking, size 300, overlap 50, Top-5 |
| Chunk-level Hit@5 | **88%** |
| Table questions answered with an interpretable citation | **75%** |
| Citations pointing at the correct article / chunk | **100% / 100%** |
| Unsupported questions refused | **6/6 (100%)** |

---

## Quick start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python main.py ingest      # build one index per chunking strategy
python main.py demo        # citations, refusals and filtering in one run
```

Everything runs locally — embeddings via `all-MiniLM-L6-v2`, generation
via `Qwen2.5-0.5B-Instruct`, vectors in a local ChromaDB. No API key is
required.

## Commands

```bash
python main.py status                          # which indexes exist

python main.py ask "How long is a password reset link valid?"
python main.py ask "How do I fix it?" --area Billing
python main.py ask "..." --top-k 3 --strategy fixed --show-retrieved

python main.py search "payment failed" --area Billing --top-k 5

python main.py ingest --chunk-size 500 --chunk-overlap 100
```

`ask` generates a grounded answer with verified citations; `search`
returns raw chunks without calling the LLM.

## Reproducing the evaluation

```bash
python experiments/evaluate_retrieval.py    # 12 configs, Top-3 vs Top-5
python experiments/evaluate_filtering.py    # filtered vs unfiltered
python experiments/calibrate_threshold.py   # refusal threshold sweep
python experiments/evaluate_generation.py   # citations + refusals
```

Each writes JSON into `evaluation/`. Every number in `results.md` comes
from these files.

---

## Layout

```
data/                     6 mock articles with YAML frontmatter
src/
  loader.py               loads any file type (pdf, docx, pptx, xlsx, csv, json, html, md, txt…) and extracts metadata
  chunking.py             3 chunking strategies + registry
  embeddings.py           cached sentence-transformers model
  vector_store.py         one ChromaDB collection per strategy
  retriever.py            similarity search + metadata filtering
  grounding.py            verifies an answer against its chunks
  generator.py            3-gate grounded answering with citations
  metrics.py              hit@k, MRR, table-grounding
evaluation/               question sets + generated result JSON
experiments/              the four evaluation scripts
main.py                   CLI application
results.md                evaluation write-up
```

## Chunking strategies

| Strategy | Description |
|---|---|
| `recursive` | `RecursiveCharacterTextSplitter` on markdown separators |
| `fixed` | `CharacterTextSplitter`, fixed character budget |
| `markdown` | **default** — section-aware, keeps troubleshooting tables intact by repeating the header row, prefixes each chunk with `Article - Section` |

`python src/chunking.py` prints the comparison. The first two strategies
strand 20 of the corpus's 24 table rows in chunks with no header row;
`markdown` strands none. See [results.md §2](results.md).

## Metadata

Every chunk carries `source_file`, `article_id`, `product_area`,
`last_updated`, plus `chunk_id`, `strategy`, `section` and `is_table`.

`product_area`, `article_id` and `is_table` are queryable filters:

```bash
python main.py search "How do I fix it?" --area Billing
python main.py ask "How long do I have to wait?" --article HC-002
```

## How refusal works

Three gates, because no single one is sufficient:

1. **Distance gate** — top-1 distance > 0.75 (calibrated, not guessed).
2. **Model gate** — the model reports the fact is not in the sources.
3. **Grounding gate** — no retrieved chunk actually supports the
   generated text.

Gate 1 alone refuses only 4 of 6 unsupported questions: two of them
retrieve *closer* than any answerable question, because the corpus
discusses the topic without ever stating the fact. See
[results.md §5](results.md).

## How citations work

Citations are **verified, not model-generated**. Each retrieved chunk is
scored against the answer for content-word coverage (≥ 0.55) and for
numbers asserted but absent. Only passing chunks are cited, so a
citation cannot point at a chunk that does not contain the answer —
measured at 100% on the evaluation set.
