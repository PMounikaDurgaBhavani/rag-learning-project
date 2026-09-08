"""Load documents, chunks and embeddings from data/ into Postgres.

Runs in three steps, each idempotent:

  1. schema      create tables and the vector extension
  2. documents   read data/ through the existing loader, upsert to Postgres
  3. chunks      chunk + embed each strategy, write vectors to Postgres

Step 3 embeds from the documents rather than importing vectors from
anywhere, which is what makes the database self-sufficient: the index can
always be rebuilt from data/ alone, so Postgres is the only thing that
needs backing up.

The verify step checks that pgvector's distances are on the scale Chroma
used, because every calibrated number in this project — RELEVANCE_THRESHOLD
= 0.75 above all — is expressed in those units.

Usage
-----
    python experiments/migrate_to_postgres.py            # all steps
    python experiments/migrate_to_postgres.py --verify   # compare only
"""

import argparse
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

import db
from loader import load_articles
from chunking import STRATEGIES, create_chunks
from embeddings import create_embeddings, embed_query


def step_schema():
    print("1. schema")
    db.init_schema()
    print(f"   ready at {db.DATABASE_URL}")


def step_documents():
    print("\n2. documents")
    documents = load_articles()   # already in the database
    if not documents:
        print("   no documents in the database — "
              "run: python main.py import <path>")
        return []

    for document in documents:
        metadata = document["metadata"]
        db.upsert_document(document)
        print(f"   {metadata['source_file']:<28} {metadata['article_id']:<12} "
              f"{len(document['content']):>6} chars")

    print(f"   {len(documents)} documents in Postgres")
    return documents


def step_chunks(documents, chunk_size=300, chunk_overlap=50):
    print("\n3. chunks + embeddings")
    if not documents:
        documents = load_articles()

    for strategy in sorted(STRATEGIES):
        chunks = create_chunks(strategy, documents, chunk_size, chunk_overlap)
        embeddings = create_embeddings(chunks, show_progress_bar=False)
        written = db.replace_chunks(strategy, chunks, embeddings)
        print(f"   {strategy:<12} {written:>5} chunks embedded and stored")


def step_verify(top_k=5, strategy="markdown"):
    """Check the store answers, and that its distances are on Chroma's scale.

    The original migration compared every query against the live ChromaDB
    index (0/5 ranking mismatches, worst drift 1.14e-06). Chroma has since
    been removed, so there is nothing left to diff against; what can still
    be checked is the property that made the migration safe in the first
    place — that a distance out of pgvector equals Chroma's squared L2, so
    RELEVANCE_THRESHOLD = 0.75 still means what it meant.
    """
    print("\n4. verify")

    import numpy as np

    queries = [
        "How long is a CloudDesk password reset link valid?",
        "My card expired and payment failed under HC-003. What is the solution?",
        "Why is the Upload File button unavailable?",
        "How do I filter CloudDesk tickets by Priority?",
        "What is the cause if CloudDesk notifications stopped suddenly?",
    ]

    worst = 0.0
    empty = 0

    for query in queries:
        vector = np.asarray(embed_query(query)[0], dtype=np.float64)
        hits = db.search(vector, top_k=top_k, strategy=strategy)

        if not hits:
            empty += 1
            print(f"   NO HITS  {query[:56]}")
            continue

        # Recompute the top hit's distance locally and compare: this is the
        # squared-L2 == 2*cosine identity the threshold depends on.
        top = hits[0]
        with db.connect() as connection:
            row = connection.execute(
                "SELECT embedding FROM chunks WHERE chunk_id = %s;",
                (top["chunk_id"],),
            ).fetchone()
        # register_vector hands back a pgvector Vector, not a list.
        raw = row[0]
        stored = np.asarray(
            raw.to_list() if hasattr(raw, "to_list") else raw,
            dtype=np.float64,
        )
        expected = float(np.sum((stored - vector) ** 2))
        delta = abs(expected - top["distance"])
        worst = max(worst, delta)

        print(f"   OK  d={top['distance']:.4f} |Δ|={delta:.2e}  "
              f"{top['chunk_id']:<22} {query[:40]}")

    print(f"\n   queries with no hits : {empty}/{len(queries)}")
    print(f"   worst distance error : {worst:.2e}")
    if empty == 0 and worst < 1e-4:
        print("   -> distances are Chroma-scale squared L2. The 0.75 gate "
              "and every recorded number keep their meaning.")
    else:
        print("   -> PROBLEM. Do not trust pre-migration thresholds until "
              "this is resolved.")
    return empty, worst


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--verify", action="store_true",
                        help="only run the distance-scale check")
    parser.add_argument("--chunk-size", type=int, default=300)
    parser.add_argument("--chunk-overlap", type=int, default=50)
    args = parser.parse_args()

    print("=" * 70)
    print("MIGRATION → PostgreSQL + pgvector")
    print("=" * 70)

    if args.verify:
        step_verify()
        return

    step_schema()
    documents = step_documents()
    step_chunks(documents, args.chunk_size, args.chunk_overlap)
    step_verify()

    print("\n" + "=" * 70)
    print("stats:", db.stats())
    print("=" * 70)


if __name__ == "__main__":
    main()
