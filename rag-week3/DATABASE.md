# PostgreSQL + pgvector

Documents, chunks and embeddings live in Postgres. It is the **only**
store. There is no `data/` directory and no `chroma_db/` directory — both
have been removed, along with the `chromadb` dependency. Uploads write a
row and leave nothing on disk.

## Why Postgres for this project

The data here is three things that would normally need three stores:
relational rows (documents), free-form metadata (frontmatter that differs
per file), and 384-dimension vectors. Postgres holds all three — JSONB for
metadata, pgvector for embeddings — so a chunk and its vector are one row
and a filtered search is a `WHERE` clause, not a metadata filter bolted
onto a vector index. It is also already installed on this machine, needs no
Docker, and the same code runs unchanged against a hosted Postgres.

## Setup

The app never assumes a database exists. `db setup` creates it — the
database, the extension and the tables — wherever `DATABASE_URL` points.

```bash
cp .env.example .env          # then edit DATABASE_URL
python main.py db setup       # creates database + extension + tables
python main.py import export  # load documents
```

Full walkthrough, including creating the server itself:
**[SETUP_DB.md](SETUP_DB.md)**

`db setup` is idempotent — safe to re-run, and the normal way to point the
app at a new database.

## Where the database lives

**There is no database yet — you create it.** Follow
[SETUP_DB.md](SETUP_DB.md) for the step-by-step; this file is the
reference for how the store works once it exists.

The application is tied to a database by exactly one thing: `DATABASE_URL`
in `.env`. Where that points is your choice —

- **its own server** — `initdb` a cluster on its own port and data
  directory, sharing nothing with other Postgres instances on the machine
- **an existing server** — another database on a Postgres already running
- **managed Postgres** — Neon, Supabase, RDS

`python main.py db setup` creates the database, the pgvector extension and
the tables at whichever of those the URL names.

## Managing it

| Command | What it does |
|---|---|
| `python main.py db setup` | Create database + extension + tables (idempotent) |
| `python main.py db status` | Reachable? Exists? What is in it? |
| `python main.py db url` | Which connection URL is in use |
| `python main.py db backup --file x.sql` | `pg_dump` to a file |
| `python main.py db restore --file x.sql` | `psql` restore |
| `python main.py db reset` | Drop and recreate the tables — asks you to type the database name |

Every one of these takes `--url` to act on a different database without
changing your environment:

```bash
python main.py db status --url postgresql://$USER@localhost:5433/other_db
```

## Getting documents in and out

| Command | What it does |
|---|---|
| `python main.py import <path>` | Read a file or folder into `documents`, then re-index |
| `python main.py export --to export` | Write every document back out as a file + `.meta.json` sidecar |
| `python main.py ingest` | Re-chunk and re-embed what is already in the database — reads no files |
| `python main.py status` | Document and chunk counts, and the connection string |

`import` is the only path from the filesystem into the app. The UI upload
extracts through a temp file that is deleted immediately; the row is the
artefact.

**The database is now the only copy of your source text.** Back it up:

```bash
pg_dump clouddesk_rag > clouddesk_rag.sql       # everything, vectors included
python main.py export --to export               # readable files + metadata
```

An exported folder re-imports with its metadata intact — verified by
deleting a document and restoring it from `export/`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | set in `.env` to port **5433** | Where the database is |

Read from the environment first, then a `.env` file at the project root, so
a one-off `DATABASE_URL=... python main.py ...` always wins over the file.
`.env` is gitignored; `.env.example` is committed as the template.

There is no backend switch. One store, one code path.

## Schema

```
documents                          chunks
----------                         ------
id            BIGSERIAL PK         chunk_id      TEXT PK
source_file   TEXT UNIQUE          document_id   BIGINT FK -> documents(id) CASCADE
article_id    TEXT                 strategy      TEXT      (markdown|recursive|fixed)
title         TEXT                 chunk_index   INTEGER
product_area  TEXT                 content       TEXT
last_updated  TEXT                 section       TEXT
file_type     TEXT                 is_table      BOOLEAN
content       TEXT                 article_id    TEXT
metadata      JSONB                product_area  TEXT
                                   metadata      JSONB
                                   embedding     vector(384)
```

Deleting a document removes its chunks by cascade. Re-indexing one strategy
happens in a single transaction, so a failure leaves the previous chunks
serving queries rather than a half-written index.

## The distance question — why the thresholds still work

Every calibrated number in this project is in ChromaDB's distance units:
`RELEVANCE_THRESHOLD = 0.75`, the recorded hit-rate@3, the failure
taxonomy. A vector store that returned a different scale would silently
invalidate all of it.

Measured against the live Chroma index before migrating:

- `all-MiniLM-L6-v2` embeddings are **unit-norm** (‖v‖ = 1.0)
- Chroma's default space is `l2` and it returns **squared** L2
- for unit vectors, ‖a−b‖² = 2(1 − cos) = **2 × cosine distance**

So `2 * (embedding <=> query)` in pgvector *is* a Chroma distance:

```sql
SELECT chunk_id, 2 * (embedding <=> %s::vector) AS distance
FROM chunks WHERE strategy = 'markdown'
ORDER BY embedding <=> %s::vector LIMIT 5;
```

Verified against the live Chroma index at migration time — 0/5 ranking
mismatches, worst drift 1.14e-06 — and the Week 4 golden set reproduced
exactly: hit-rate@3 66.7% → 75.0%, tally R=3 / G=0 / NIC=1, identical
per-question ranks.

Chroma is gone now, so there is nothing left to diff against; what the
check still verifies is the property the threshold depends on — that a
distance out of pgvector equals squared L2 (worst error 1.16e-06):

```bash
python experiments/migrate_to_postgres.py --verify
```

## Cost of the move

Retrieval p50 went from ~12 ms to ~30 ms — a client/server round trip and
an exact scan, against Chroma running in-process. Both arms of the Week 4
comparison pay it equally, so the +8.3pp hit-rate delta is unchanged.

There is no ANN index by default: on ~120 chunks an exact scan is faster
than HNSW *and* exactly reproducible. Past roughly 10k chunks, opt in —
the query expression does not change:

```python
import db; db.create_ann_index()
```

## Useful queries

```sql
SELECT strategy, count(*) FROM chunks GROUP BY 1;
SELECT article_id, count(*) FROM chunks WHERE strategy='markdown' GROUP BY 1;
SELECT chunk_id, section FROM chunks WHERE is_table AND strategy='markdown';
SELECT source_file, metadata FROM documents WHERE product_area='Billing';
```

## What was removed

| Gone | Replaced by |
|---|---|
| `chroma_db/` (20 MB) | `chunks.embedding` — `vector(384)` |
| `data/` | `documents` table |
| `.meta.json` sidecars | `documents.metadata` JSONB (sidecars now only exist inside an export) |
| `chromadb` dependency | `psycopg` + `pgvector` |

Verified after deletion: `ask`, `ingest`, `status`, upload, delete and the
Week 4 golden set all run with neither directory present, and an upload
creates no files in the repo.

## Not yet in the database

Week 5 **traces** still go to `traces/traces.jsonl` (plus optional SQLite
via `RAG_TRACE_DB`). Moving them into this Postgres is a small change —
one table, same connection — say so and it can share this database.
