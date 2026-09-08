"""Build and inspect the vector index. PostgreSQL + pgvector is the store.

There is no second backend. Documents, chunks and embeddings all live in
one database (see src/db.py and DATABASE.md), so "rebuild the index" is a
transaction rather than a directory of files that has to be kept in step
with data/.

Index layout: one `chunks` table, separated logically by the `strategy`
column rather than by three collections. Rebuilding one strategy touches
only its rows, and the strategies can be compared with a GROUP BY instead
of three round trips.
"""

import db
from loader import load_articles
from chunking import STRATEGIES, create_chunks
from embeddings import create_embeddings


# The strategy the application queries unless told otherwise.
DEFAULT_STRATEGY = "markdown"


def index_label(strategy):
    """Human-readable name for one strategy's slice of the chunks table."""
    return f"chunks[strategy={strategy}]"


def build_index(
    strategy,
    chunk_size=300,
    chunk_overlap=50,
    show_progress_bar=False
):
    """Re-chunk, re-embed and replace one strategy's chunks.

    The replace happens in a single transaction: a failure part-way
    through leaves the previous chunks answering queries rather than a
    half-written index that silently returns fewer results.
    """

    db.init_schema()

    documents = load_articles()

    chunks = create_chunks(
        strategy,
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    embeddings = create_embeddings(
        chunks,
        show_progress_bar=show_progress_bar
    )

    written = db.replace_chunks(strategy, chunks, embeddings)

    return {
        "strategy": strategy,
        "collection": index_label(strategy),
        "chunks": written,
    }


def build_all_indexes(chunk_size=300, chunk_overlap=50):
    """Build one index per chunking strategy over the same documents."""

    db.init_schema()

    results = []

    for strategy in STRATEGIES:

        result = build_index(
            strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

        print(f"  {strategy:<12} -> {result['collection']:<28} "
              f"{result['chunks']:>5} chunks")

        results.append(result)

    return results


def index_counts():
    """Chunks per strategy, straight from the database."""
    try:
        return db.stats()["chunks"]
    except Exception:
        return {}


if __name__ == "__main__":

    print("=" * 70)
    print("BUILDING INDEXES  (PostgreSQL + pgvector)")
    print("=" * 70)
    print(f"database: {db.DATABASE_URL}\n")

    build_all_indexes()

    print(f"\nstats: {db.stats()}")
    print(f"default strategy for queries: {DEFAULT_STRATEGY}")
