"""Compare chunking strategies, chunk sizes, overlaps and Top-K values.

Every configuration is indexed into its own in-memory ChromaDB
collection built from the same 6 documents, then scored against the
same 8 known-answer questions.

Metrics
-------
article_hit@k   expected article appears in the top k chunks
chunk_hit@k     a top-k chunk literally contains the answer text
mrr             1 / rank of the first chunk containing the answer
table_ok@5      for table questions: the answer row AND the table
                header row are in the same retrieved chunk
"""

import json
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "src")
    )
)

import chromadb

from loader import load_articles
from chunking import STRATEGIES, create_chunks
from embeddings import embed_texts, embed_query
from retriever import format_results
from vector_store import clean_metadata
from metrics import (
    article_hit_at_k,
    hit_at_k,
    is_table_grounded,
    reciprocal_rank,
    percent,
)


EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "evaluation")
)

QUESTIONS_FILE = os.path.join(EVAL_DIR, "questions.json")
RESULTS_FILE = os.path.join(EVAL_DIR, "retrieval_results.json")

# (chunk_size, chunk_overlap) combinations to sweep.
SIZE_CONFIGS = [
    (200, 0),
    (300, 50),
    (500, 100),
    (800, 150),
]

TOP_K_VALUES = [3, 5]


def load_questions(path=QUESTIONS_FILE):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


# ============================================================
# Index one configuration into an ephemeral collection
# ============================================================

def index_config(client, strategy, chunk_size, chunk_overlap, documents):

    chunks = create_chunks(
        strategy,
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    name = f"eval_{strategy}_{chunk_size}_{chunk_overlap}"

    collection = client.get_or_create_collection(name=name)

    texts = [chunk["content"] for chunk in chunks]

    collection.upsert(
        ids=[
            chunk["metadata"]["chunk_id"]
            for chunk in chunks
        ],
        documents=texts,
        embeddings=embed_texts(texts).tolist(),
        metadatas=[
            clean_metadata(chunk["metadata"])
            for chunk in chunks
        ]
    )

    return collection, chunks


# ============================================================
# Score one configuration
# ============================================================

def evaluate_config(collection, chunks, questions, label):

    scores = {
        "article_hit": {k: [] for k in TOP_K_VALUES},
        "chunk_hit": {k: [] for k in TOP_K_VALUES},
        "table_ok": {k: [] for k in TOP_K_VALUES},
    }

    reciprocal_ranks = []
    failures = []

    max_k = max(TOP_K_VALUES)

    for question in questions:

        retrieved = format_results(
            collection.query(
                query_embeddings=embed_query(question["question"]),
                n_results=max_k
            )
        )

        for k in TOP_K_VALUES:

            scores["article_hit"][k].append(
                article_hit_at_k(retrieved, question, k)
            )

            scores["chunk_hit"][k].append(
                hit_at_k(retrieved, question, k)
            )

            if question["type"] == "table":
                scores["table_ok"][k].append(
                    hit_at_k(
                        retrieved,
                        question,
                        k,
                        predicate=is_table_grounded
                    )
                )

        reciprocal_ranks.append(
            reciprocal_rank(retrieved, question)
        )

        # Record anything that did not produce a usable chunk at k=5.
        if not hit_at_k(retrieved, question, max_k):

            failures.append(
                {
                    "config": label,
                    "question_id": question["id"],
                    "question": question["question"],
                    "type": question["type"],
                    "expected_article_id": question["expected_article_id"],
                    "answer_keys": question["answer_keys"],
                    "retrieved": [
                        {
                            "rank": chunk["rank"],
                            "chunk_id": chunk["chunk_id"],
                            "distance": round(chunk["distance"], 4),
                        }
                        for chunk in retrieved
                    ],
                    "article_found": article_hit_at_k(
                        retrieved, question, max_k
                    ),
                }
            )

        elif (
            question["type"] == "table"
            and not hit_at_k(
                retrieved, question, max_k, predicate=is_table_grounded
            )
        ):

            failures.append(
                {
                    "config": label,
                    "question_id": question["id"],
                    "question": question["question"],
                    "type": "table_header_lost",
                    "expected_article_id": question["expected_article_id"],
                    "answer_keys": question["answer_keys"],
                    "retrieved": [
                        {
                            "rank": chunk["rank"],
                            "chunk_id": chunk["chunk_id"],
                            "distance": round(chunk["distance"], 4),
                        }
                        for chunk in retrieved
                    ],
                    "article_found": True,
                }
            )

    sizes = [len(chunk["content"]) for chunk in chunks]

    result = {
        "label": label,
        "chunks": len(chunks),
        "avg_chunk_size": sum(sizes) // len(sizes),
        "mrr": sum(reciprocal_ranks) / len(reciprocal_ranks),
    }

    for k in TOP_K_VALUES:
        result[f"article_hit@{k}"] = percent(scores["article_hit"][k])
        result[f"chunk_hit@{k}"] = percent(scores["chunk_hit"][k])
        result[f"table_ok@{k}"] = percent(scores["table_ok"][k])

    return result, failures


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    print("=" * 78)
    print("RAG RETRIEVAL EVALUATION")
    print("=" * 78)

    documents = load_articles()
    questions = load_questions()

    table_questions = [
        question
        for question in questions
        if question["type"] == "table"
    ]

    print(f"Articles          : {len(documents)}")
    print(f"Questions         : {len(questions)}")
    print(f"Table questions   : {len(table_questions)}")
    print(
        f"Configurations    : "
        f"{len(STRATEGIES) * len(SIZE_CONFIGS)}"
    )

    client = chromadb.EphemeralClient()

    all_results = []
    all_failures = []

    for strategy in STRATEGIES:

        for chunk_size, chunk_overlap in SIZE_CONFIGS:

            label = f"{strategy}/{chunk_size}/{chunk_overlap}"

            collection, chunks = index_config(
                client,
                strategy,
                chunk_size,
                chunk_overlap,
                documents
            )

            result, failures = evaluate_config(
                collection,
                chunks,
                questions,
                label
            )

            result["strategy"] = strategy
            result["chunk_size"] = chunk_size
            result["chunk_overlap"] = chunk_overlap

            all_results.append(result)
            all_failures.extend(failures)

            print(f"  scored {label}")

    # --------------------------------------------------------
    # Results table
    # --------------------------------------------------------

    print("\n" + "=" * 78)
    print("RESULTS (8 questions, 4 of them table-based)")
    print("=" * 78)

    header = (
        f"{'config':<22}"
        f"{'chunks':>7}"
        f"{'art@3':>8}"
        f"{'art@5':>8}"
        f"{'chk@3':>8}"
        f"{'chk@5':>8}"
        f"{'tbl@5':>8}"
        f"{'MRR':>8}"
    )

    print(header)
    print("-" * 78)

    for result in all_results:
        print(
            f"{result['label']:<22}"
            f"{result['chunks']:>7}"
            f"{result['article_hit@3']:>7.0f}%"
            f"{result['article_hit@5']:>7.0f}%"
            f"{result['chunk_hit@3']:>7.0f}%"
            f"{result['chunk_hit@5']:>7.0f}%"
            f"{result['table_ok@5']:>7.0f}%"
            f"{result['mrr']:>8.3f}"
        )

    # --------------------------------------------------------
    # Best configuration
    # --------------------------------------------------------

    best = max(
        all_results,
        key=lambda r: (r["chunk_hit@5"], r["table_ok@5"], r["mrr"])
    )

    print("\n" + "=" * 78)
    print("BEST CONFIGURATION")
    print("=" * 78)
    print(f"  {best['label']}")
    print(f"  chunk_hit@5 : {best['chunk_hit@5']:.0f}%")
    print(f"  table_ok@5  : {best['table_ok@5']:.0f}%")
    print(f"  MRR         : {best['mrr']:.3f}")

    # --------------------------------------------------------
    # Failures
    # --------------------------------------------------------

    print("\n" + "=" * 78)
    print(f"RETRIEVAL FAILURES ({len(all_failures)} across all configs)")
    print("=" * 78)

    for failure in all_failures[:25]:
        print(
            f"  {failure['config']:<22} "
            f"{failure['question_id']} "
            f"({failure['type']}) "
            f"article_found={failure['article_found']}"
        )

    if len(all_failures) > 25:
        print(f"  ... and {len(all_failures) - 25} more (see JSON)")

    # --------------------------------------------------------
    # Persist
    # --------------------------------------------------------

    with open(RESULTS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {
                "configs": all_results,
                "best": best["label"],
                "failures": all_failures,
            },
            file,
            indent=2
        )

    print(f"\nSaved -> {RESULTS_FILE}")
