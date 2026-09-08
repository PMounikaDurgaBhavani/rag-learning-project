"""PostgreSQL + pgvector store for documents, chunks and embeddings.

Why Postgres for this project
-----------------------------
The data here is three things that usually need three stores: relational
rows (documents), free-form metadata (frontmatter that differs per file),
and 384-dimension vectors. Postgres holds all three — JSONB for the
metadata, pgvector for the embeddings — so a chunk and its vector live in
one row and a filtered search is a WHERE clause rather than a metadata
filter bolted onto a vector index.

Matching ChromaDB's distances exactly
-------------------------------------
This matters more than it sounds. Every calibrated number in this project
is expressed in Chroma's distance units — RELEVANCE_THRESHOLD = 0.75, the
recorded hit-rate@3, the failure taxonomy. If the move to pgvector shifted
the scale, all of it would silently become meaningless.

Measured against the live Chroma index:

    all-MiniLM-L6-v2 embeddings are unit-norm (verified: ||v|| = 1.0)
    Chroma's default space is l2, and it returns SQUARED L2
    for unit vectors:  ||a-b||^2  ==  2 * (1 - cos)  ==  2 * cosine_distance

So `2 * (embedding <=> query)` in pgvector reproduces a Chroma distance to
floating-point noise. The cosine operator is used rather than `<->`
squared because it lets the HNSW index be built for cosine and stay usable
by the same expression.
"""

import json
import os
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

def _load_dotenv(path=None):
    """Read DATABASE_URL (and friends) out of a .env file.

    Kept dependency-free and non-overriding: a value already exported in
    the shell wins, so a one-off `DATABASE_URL=... python main.py ...`
    still works without editing the file.
    """
    from pathlib import Path

    candidate = Path(path or Path(__file__).resolve().parent.parent / ".env")
    if not candidate.exists():
        return

    for line in candidate.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()

# The only thing tying the app to a database. Point it anywhere — another
# port, another host, a managed Postgres — and nothing else changes.
DEFAULT_DATABASE_URL = (
    f"postgresql://{os.environ.get('USER', 'postgres')}"
    f"@localhost:5432/clouddesk_rag"
)

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)

EMBEDDING_DIM = 384          # all-MiniLM-L6-v2

# Chroma returns squared L2; for unit vectors that is 2x cosine distance.
CHROMA_DISTANCE_SQL = "2 * (c.embedding <=> %s::vector)"


SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL PRIMARY KEY,
    source_file   TEXT UNIQUE NOT NULL,
    article_id    TEXT NOT NULL,
    title         TEXT,
    product_area  TEXT,
    last_updated  TEXT,
    file_type     TEXT,
    content       TEXT NOT NULL,
    -- Frontmatter varies per file and new keys appear with new uploads,
    -- so the extras stay schemaless rather than forcing a migration.
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_article ON documents(article_id);
CREATE INDEX IF NOT EXISTS idx_documents_area    ON documents(product_area);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id      TEXT PRIMARY KEY,
    document_id   BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    strategy      TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    content       TEXT NOT NULL,
    section       TEXT,
    is_table      BOOLEAN NOT NULL DEFAULT FALSE,
    article_id    TEXT NOT NULL,
    product_area  TEXT,
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding     vector({EMBEDDING_DIM}),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(strategy);
CREATE INDEX IF NOT EXISTS idx_chunks_article  ON chunks(article_id);
CREATE INDEX IF NOT EXISTS idx_chunks_area     ON chunks(product_area);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
"""

# Built separately: an ANN index is approximate, and on a corpus this size
# (hundreds of chunks) exact scan is both faster and exactly reproducible.
# Create it when the corpus grows — the query expression does not change.
HNSW_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"""


@contextmanager
def connect(url=None, autocommit=True):
    """A connection with pgvector types registered when they exist.

    Registration is best-effort because setup has to connect to a database
    that does not have the extension yet in order to create it. Anything
    that actually round-trips a vector runs after setup, by which point
    the type is there; queries that touch no vector column (counts,
    document reads) never needed it.
    """
    connection = psycopg.connect(url or DATABASE_URL, autocommit=autocommit)
    try:
        try:
            register_vector(connection)
        except psycopg.ProgrammingError:
            pass                      # extension not installed yet
        yield connection
    finally:
        connection.close()


def init_schema(url=None):
    with connect(url) as connection:
        connection.execute(SCHEMA)
    return True


def create_ann_index(url=None):
    """Opt in to approximate search. Only worth it past ~10k chunks."""
    with connect(url) as connection:
        connection.execute(HNSW_INDEX)
    return True


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def upsert_document(document, connection=None, url=None):
    """Insert or update one document, keyed on source_file."""

    metadata = dict(document["metadata"])
    content = document["content"]

    known = ("source_file", "article_id", "title", "product_area",
             "last_updated", "file_type")
    extras = {k: v for k, v in metadata.items() if k not in known}

    sql = """
        INSERT INTO documents (source_file, article_id, title, product_area,
                               last_updated, file_type, content, metadata)
        VALUES (%(source_file)s, %(article_id)s, %(title)s, %(product_area)s,
                %(last_updated)s, %(file_type)s, %(content)s, %(metadata)s)
        ON CONFLICT (source_file) DO UPDATE SET
            article_id   = EXCLUDED.article_id,
            title        = EXCLUDED.title,
            product_area = EXCLUDED.product_area,
            last_updated = EXCLUDED.last_updated,
            file_type    = EXCLUDED.file_type,
            content      = EXCLUDED.content,
            metadata     = EXCLUDED.metadata,
            updated_at   = now()
        RETURNING id;
    """
    params = {
        "source_file": metadata.get("source_file"),
        "article_id": metadata.get("article_id"),
        "title": metadata.get("title"),
        "product_area": metadata.get("product_area"),
        "last_updated": str(metadata.get("last_updated") or ""),
        "file_type": metadata.get("file_type"),
        "content": content,
        "metadata": json.dumps(extras, default=str),
    }

    if connection is not None:
        return connection.execute(sql, params).fetchone()[0]

    with connect(url) as own:
        return own.execute(sql, params).fetchone()[0]


def load_documents(url=None):
    """Every document, in the shape load_articles() returns."""

    with connect(url) as connection:
        rows = connection.execute("""
            SELECT source_file, article_id, title, product_area,
                   last_updated, file_type, content, metadata
            FROM documents ORDER BY source_file;
        """).fetchall()

    documents = []
    for (source_file, article_id, title, product_area,
         last_updated, file_type, content, metadata) in rows:
        merged = dict(metadata or {})
        merged.update({
            "source_file": source_file,
            "article_id": article_id,
            "title": title,
            "product_area": product_area,
            "last_updated": last_updated,
            "file_type": file_type,
        })
        documents.append({"content": content, "metadata": merged})
    return documents


def delete_document(source_file, url=None):
    """Remove a document; its chunks go with it via ON DELETE CASCADE."""
    with connect(url) as connection:
        result = connection.execute(
            "DELETE FROM documents WHERE source_file = %s;", (source_file,)
        )
        return result.rowcount


# ---------------------------------------------------------------------------
# chunks + embeddings
# ---------------------------------------------------------------------------

def replace_chunks(strategy, chunks, embeddings, url=None):
    """Rebuild one strategy's chunks in a single transaction.

    All-or-nothing: a failed re-index leaves the previous chunks in place
    rather than a half-written index that still answers queries.
    """

    with connect(url, autocommit=False) as connection:
        try:
            connection.execute(
                "DELETE FROM chunks WHERE strategy = %s;", (strategy,)
            )

            doc_ids = {
                source_file: doc_id
                for doc_id, source_file in connection.execute(
                    "SELECT id, source_file FROM documents;"
                ).fetchall()
            }

            rows = []
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                metadata = chunk["metadata"]
                source_file = metadata.get("source_file")
                if source_file not in doc_ids:
                    raise KeyError(
                        f"chunk {metadata.get('chunk_id')} references "
                        f"{source_file!r}, which is not in documents"
                    )
                rows.append((
                    metadata["chunk_id"],
                    doc_ids[source_file],
                    strategy,
                    index,
                    chunk["content"],
                    metadata.get("section"),
                    bool(metadata.get("is_table", False)),
                    metadata.get("article_id"),
                    metadata.get("product_area"),
                    json.dumps(metadata, default=str),
                    list(map(float, embedding)),
                ))

            with connection.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO chunks (chunk_id, document_id, strategy,
                           chunk_index, content, section, is_table, article_id,
                           product_area, metadata, embedding)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    rows,
                )
            connection.commit()
            return len(rows)
        except Exception:
            connection.rollback()
            raise


def count_chunks(strategy=None, url=None):
    with connect(url) as connection:
        if strategy:
            row = connection.execute(
                "SELECT count(*) FROM chunks WHERE strategy = %s;", (strategy,)
            ).fetchone()
        else:
            row = connection.execute("SELECT count(*) FROM chunks;").fetchone()
    return row[0]


def search(query_embedding, top_k=5, strategy="markdown",
           product_area=None, article_id=None, url=None):
    """Nearest chunks, in Chroma's distance units.

    See the module docstring: `2 * (embedding <=> q)` is Chroma's squared-L2
    for unit-norm vectors, so the 0.75 threshold keeps its meaning.
    """

    vector = list(map(float, query_embedding))

    conditions = ["strategy = %s", "embedding IS NOT NULL"]
    params = [vector, strategy]

    if product_area:
        conditions.append("product_area = %s")
        params.append(product_area)
    if article_id:
        conditions.append("article_id = %s")
        params.append(article_id)

    sql = f"""
        SELECT c.chunk_id, c.content, c.section, c.is_table, c.article_id,
               c.product_area, c.metadata,
               {CHROMA_DISTANCE_SQL} AS distance
        FROM chunks c
        WHERE {' AND '.join(conditions)}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s;
    """
    params.append(vector)
    params.append(top_k)

    with connect(url) as connection:
        rows = connection.execute(sql, params).fetchall()

    results = []
    for rank, row in enumerate(rows, start=1):
        (chunk_id, content, section, is_table, article_id_value,
         product_area_value, metadata, distance) = row
        result = dict(metadata or {})
        result.update({
            "rank": rank,
            "chunk_id": chunk_id,
            "content": content,
            "section": section,
            "is_table": is_table,
            "article_id": article_id_value,
            "product_area": product_area_value,
            "distance": float(distance),
        })
        results.append(result)
    return results


def all_chunks(strategy="markdown", url=None):
    """Every chunk for a strategy — what the BM25 index is built from."""

    with connect(url) as connection:
        rows = connection.execute(
            """SELECT chunk_id, content, section, is_table, article_id,
                      product_area, metadata
               FROM chunks WHERE strategy = %s ORDER BY chunk_index;""",
            (strategy,),
        ).fetchall()

    chunks = []
    for (chunk_id, content, section, is_table, article_id,
         product_area, metadata) in rows:
        record = dict(metadata or {})
        record.update({
            "chunk_id": chunk_id,
            "content": content,
            "section": section,
            "is_table": is_table,
            "article_id": article_id,
            "product_area": product_area,
        })
        chunks.append(record)
    return chunks


def stats(url=None):
    """Counts, or zeroes if the tables are not there yet."""
    try:
        with connect(url) as connection:
            documents = connection.execute(
                "SELECT count(*) FROM documents;").fetchone()[0]
            by_strategy = connection.execute(
                """SELECT strategy, count(*) FROM chunks
                   GROUP BY strategy ORDER BY strategy;""").fetchall()
        return {"documents": documents, "chunks": dict(by_strategy)}
    except psycopg.errors.UndefinedTable:
        return {"documents": 0, "chunks": {}}


# ---------------------------------------------------------------------------
# export / import
# ---------------------------------------------------------------------------

def export_documents(directory="export", url=None):
    """Write every document back out as a file plus a metadata sidecar.

    With `data/` gone the database holds the only copy of the source text,
    so there has to be a way back out that does not involve psql. Run this
    before anything destructive, and to move a corpus between databases.
    """
    from pathlib import Path

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    written = []
    for document in load_documents(url):
        metadata = document["metadata"]
        name = metadata.get("source_file") or f"{metadata['article_id']}.md"

        (target / name).write_text(document["content"], encoding="utf-8")
        (target / f"{name}.meta.json").write_text(
            json.dumps(metadata, indent=2, default=str), encoding="utf-8"
        )
        written.append(name)

    return written


def import_file(path, product_area=None, url=None):
    """Read one file from disk into the documents table.

    This is the only remaining path from the filesystem into the app, and
    it is a deliberate one-shot: extraction needs a real path, so callers
    that start from bytes should write a temp file and point here.
    """
    from pathlib import Path

    from loader import (
        extract_text_from_file,
        split_frontmatter,
        derive_title,
        meta_path_for,
        TEXT_FRONTMATTER_EXTS,
    )
    from datetime import datetime
    import re as _re

    path = Path(path)
    extracted = extract_text_from_file(path)

    existing, body = split_frontmatter(
        extracted, allow=path.suffix.lower() in TEXT_FRONTMATTER_EXTS
    )

    article_id = _re.sub(
        r"[^A-Z0-9\-_]+", "-", path.stem.upper()
    ).strip("-") or "DOC"

    metadata = {
        "source_file": path.name,
        "article_id": article_id,
        "title": derive_title(path, body or extracted),
        "product_area": product_area or "Custom Upload",
        "last_updated": datetime.now().strftime("%Y-%m-%d"),
        "file_type": path.suffix.lower().lstrip(".") or "txt",
    }
    metadata.update({k: v for k, v in existing.items() if v not in (None, "")})

    # A sidecar next to the file still wins, so an exported corpus
    # re-imports with the metadata it left with.
    sidecar = meta_path_for(path)
    if sidecar.exists():
        try:
            metadata.update(json.loads(sidecar.read_text(encoding="utf-8")))
        except Exception:
            pass

    upsert_document(
        {"content": (body or extracted).strip(), "metadata": metadata},
        url=url,
    )
    return metadata


def import_directory(directory, product_area=None, url=None):
    """Import every readable file in a directory."""
    from pathlib import Path

    from loader import ExtractionError, META_SUFFIX

    imported, skipped = [], []
    for path in sorted(Path(directory).glob("*")):
        if path.is_dir() or path.name.startswith("."):
            continue
        if path.name.endswith(META_SUFFIX):
            continue
        try:
            imported.append(import_file(path, product_area, url))
        except ExtractionError as error:
            skipped.append((path.name, str(error)))
    return imported, skipped


# ---------------------------------------------------------------------------
# provisioning
# ---------------------------------------------------------------------------

def parse_url(url=None):
    """Split a connection URL into its parts, for display and for CREATE."""
    from urllib.parse import urlparse

    parsed = urlparse(url or DATABASE_URL)
    return {
        "scheme": parsed.scheme,
        "user": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "/").lstrip("/"),
        "query": parsed.query,
    }


def safe_url(url=None):
    """The URL with the password masked, for printing."""
    parts = parse_url(url)
    auth = parts["user"] or ""
    if parts["password"]:
        auth += ":***"
    at = "@" if auth else ""
    query = f"?{parts['query']}" if parts["query"] else ""
    return (f"{parts['scheme']}://{auth}{at}{parts['host']}:"
            f"{parts['port']}/{parts['database']}{query}")


def _maintenance_url(url=None):
    """The same server, but the always-present `postgres` database.

    A database cannot be created from inside itself, so provisioning
    connects here first.
    """
    parts = parse_url(url)
    auth = parts["user"] or ""
    if parts["password"]:
        auth += f":{parts['password']}"
    at = "@" if auth else ""
    query = f"?{parts['query']}" if parts["query"] else ""
    return (f"{parts['scheme']}://{auth}{at}{parts['host']}:"
            f"{parts['port']}/postgres{query}")


def database_exists(url=None):
    name = parse_url(url)["database"]
    try:
        with psycopg.connect(_maintenance_url(url), autocommit=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s;", (name,)
            ).fetchone()
            return row is not None
    except psycopg.OperationalError:
        return False


def server_reachable(url=None):
    try:
        with psycopg.connect(_maintenance_url(url), autocommit=True) as connection:
            connection.execute("SELECT 1;")
        return True
    except psycopg.OperationalError:
        return False


def create_database(url=None):
    """CREATE DATABASE if it is not already there. Returns True if created.

    Managed providers (Neon, Supabase, RDS) hand you a database that
    already exists and often refuse CREATE DATABASE — that is not an
    error here, it just means there was nothing to do.
    """
    name = parse_url(url)["database"]

    if database_exists(url):
        return False

    with psycopg.connect(_maintenance_url(url), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}";')
    return True


def setup(url=None):
    """Create the database if needed, enable pgvector, create the tables.

    Idempotent — safe to run against an existing database, and the normal
    way to point the app at a new one.
    """
    steps = {"url": safe_url(url)}

    if not server_reachable(url):
        parts = parse_url(url)
        raise ConnectionError(
            f"No PostgreSQL server answering at {parts['host']}:{parts['port']}. "
            f"Start one, or set DATABASE_URL to a server that is running."
        )

    steps["created_database"] = create_database(url)

    # Plain connection: the vector type cannot be registered until the
    # statement on the next line has created it.
    with psycopg.connect(url or DATABASE_URL, autocommit=True) as connection:
        try:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        except psycopg.errors.InsufficientPrivilege as error:
            raise PermissionError(
                "Could not CREATE EXTENSION vector — the role needs "
                "superuser, or the provider must enable pgvector for you "
                f"({error})"
            ) from error
        version = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector';"
        ).fetchone()
        steps["pgvector"] = version[0] if version else None

    init_schema(url)
    steps["stats"] = stats(url)
    return steps


def reset(url=None, confirm=False):
    """Drop the tables and rebuild them empty. Documents are lost."""
    if not confirm:
        raise ValueError("reset() refuses to run without confirm=True")

    with connect(url) as connection:
        connection.execute("DROP TABLE IF EXISTS chunks;")
        connection.execute("DROP TABLE IF EXISTS documents;")
    init_schema(url)
    return True
