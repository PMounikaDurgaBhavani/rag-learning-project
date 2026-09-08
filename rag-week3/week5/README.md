# Week 5 — Error analysis: how to run it, and where traces live

## The loop

```bash
# 0. every answer is now traced automatically (CLI, UI, experiments)
python main.py ask "how do I reset my password?"
python main.py ui                       # UI traffic is traced too

# 1. see how much you have
python experiments/error_analysis.py stats

# 2. draw the sample — paste the seed into your write-up
python experiments/error_analysis.py sample --seed 20260907 --n 20

# 3. prove replayability
python experiments/error_analysis.py replay --trace-id <id>

# 4. read the 20 and write your sentences in week5/notes_template.md
#    -> rename to notes.md when done. CHANGE NO CODE during this step.

# 5. cluster into 4-7 modes in week5/taxonomy.md

# 6. commit the prediction BEFORE any fix
git add week5/prediction.md && git commit -m "week5: dated prediction before fix"
git rev-parse --short HEAD              # paste this hash into notes.md
```

## What a trace carries

Everything needed to replay without the live system:

| Field | Why it is there |
|---|---|
| `prompt.version` + `prompt.sha` | a prompt edit that forgets the version bump still shows as a new sha |
| `prompt.messages` | the **literal** text sent. Replay reads this instead of rebuilding it — otherwise a re-chunked index silently changes what "the same trace" means |
| `retrieval[]` | chunk_id, distance, bm25, rrf, rerank, and both source ranks |
| `model.name` + `model.params` | greedy decoding is deterministic, so replay should be byte-identical |
| `generation.raw_output` | what the model said **before** any gate touched it |
| `outcome` | final answer, refusal reason, gate bypass, grounding scores |
| `app_version` | git sha — a taxonomy is only meaningful against a known build |

`latency_ms.total` includes model load on the first call of a process. Ignore
the first trace of any run when reasoning about latency.

## Where the data is stored

### Now: JSONL (default, always on)

`traces/traces.jsonl` — one JSON object per line, appended.

Good because it is append-only (a crash truncates one line, not the file),
greppable, and trivially replayable. Fine to a few hundred thousand rows.

```bash
RAG_TRACE_DIR=traces        # where to write   (default: traces/)
RAG_TRACE=0                 # disable tracing entirely
```

### Separate DB: SQLite (optional, one env var)

```bash
export RAG_TRACE_DB=traces/traces.db
python main.py ask "..."          # now written to BOTH sinks
```

The table is created on first write. Hot fields are real columns; the whole
record is also kept in a `payload` JSON column, so nothing is lost to the
flattening:

```sql
SELECT refusal_reason, COUNT(*) FROM traces GROUP BY 1 ORDER BY 2 DESC;
SELECT trace_id, query FROM traces WHERE query LIKE '%billing%' AND refused=1;
SELECT AVG(latency_ms) FROM traces WHERE retrieval_mode='hybrid_rerank';
SELECT json_extract(payload,'$.retrieval[0].chunk_id') FROM traces LIMIT 5;
```

JSONL stays the source of truth, so the DB can be dropped and rebuilt:

```python
import tracing; tracing.backfill_sqlite()     # replays the JSONL into SQLite
```

That is deliberate — it means adding a column later costs nothing, and it is
why the SQLite sink can stay optional without risking data loss.

### When to move past SQLite

| Situation | Use |
|---|---|
| One machine, < ~1M traces, you are the only reader | **SQLite** — no server, one file, real SQL |
| Analytics over big JSONL, no ingest step wanted | **DuckDB** — `SELECT * FROM 'traces/*.jsonl'` directly |
| Several app instances writing at once, team needs shared access | **Postgres** — SQLite's single-writer lock is the thing that breaks first |
| You want trace UI, diffing and eval runs without building them | **Langfuse** or **Arize Phoenix** (both self-hostable, OTel-based) |

SQLite's limit in practice is concurrent **writers**, not size. One process
writing and many reading is fine; two server instances writing is not.

## What this tooling deliberately does not do

It does not write your observation sentences and it does not name your
failure modes. Those are 55 of the 100 marks, they require reading, and a
tool that guessed at them would hand you back exactly the categories you
already expected — the specific failure this week exists to prevent.
