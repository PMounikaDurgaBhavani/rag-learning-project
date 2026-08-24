"""Compare filtered and unfiltered retrieval.

Three demonstrations:

1. Correct filter    - filter each question to its own product_area.
2. Wrong filter      - filter to a different product_area, proving the
                       filter is really applied and can break retrieval.
3. Ambiguous queries - a query with no subject ("How do I fix it?")
                       is scoped by metadata instead of by wording.
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

from retriever import retrieve_chunks, build_where
from vector_store import DEFAULT_STRATEGY
from metrics import hit_at_k, reciprocal_rank, percent


EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "evaluation")
)

QUESTIONS_FILE = os.path.join(EVAL_DIR, "questions.json")
AMBIGUOUS_FILE = os.path.join(EVAL_DIR, "ambiguous_questions.json")
RESULTS_FILE = os.path.join(EVAL_DIR, "filtering_results.json")

TOP_K = 5

WRONG_AREA = {
    "Authentication": "Storage",
    "Billing": "Notifications",
    "Notifications": "Billing",
    "Storage": "Authentication",
    "Ticket Management": "Billing",
}


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def chunk_ids(chunks):
    return [chunk["chunk_id"] for chunk in chunks]


def off_topic_count(chunks, expected_area):
    return sum(
        1
        for chunk in chunks
        if chunk["product_area"] != expected_area
    )


# ============================================================
# 1 + 2. Correct filter and wrong filter
# ============================================================

def compare_filters(questions):

    rows = []

    unfiltered_hits_3 = []
    unfiltered_hits_5 = []
    filtered_hits_3 = []
    filtered_hits_5 = []
    wrong_hits_5 = []

    unfiltered_mrr = []
    filtered_mrr = []

    print("=" * 78)
    print("1. UNFILTERED vs FILTERED (filter = question's product_area)")
    print("=" * 78)

    print(
        f"{'Q':<4}"
        f"{'product_area':<18}"
        f"{'unfiltered':>12}"
        f"{'filtered':>10}"
        f"{'wrong':>8}"
        f"{'off-topic':>11}"
        f"{'changed':>9}"
    )

    print("-" * 78)

    for question in questions:

        area = question["expected_product_area"]

        unfiltered = retrieve_chunks(
            question["question"],
            top_k=TOP_K,
            strategy=DEFAULT_STRATEGY
        )

        filtered = retrieve_chunks(
            question["question"],
            top_k=TOP_K,
            strategy=DEFAULT_STRATEGY,
            where=build_where(product_area=area)
        )

        wrong = retrieve_chunks(
            question["question"],
            top_k=TOP_K,
            strategy=DEFAULT_STRATEGY,
            where=build_where(product_area=WRONG_AREA[area])
        )

        u_hit_3 = hit_at_k(unfiltered, question, 3)
        u_hit_5 = hit_at_k(unfiltered, question, 5)
        f_hit_3 = hit_at_k(filtered, question, 3)
        f_hit_5 = hit_at_k(filtered, question, 5)
        w_hit_5 = hit_at_k(wrong, question, 5)

        unfiltered_hits_3.append(u_hit_3)
        unfiltered_hits_5.append(u_hit_5)
        filtered_hits_3.append(f_hit_3)
        filtered_hits_5.append(f_hit_5)
        wrong_hits_5.append(w_hit_5)

        unfiltered_mrr.append(reciprocal_rank(unfiltered, question))
        filtered_mrr.append(reciprocal_rank(filtered, question))

        changed = len(
            set(chunk_ids(unfiltered)) ^ set(chunk_ids(filtered))
        )

        off_topic = off_topic_count(unfiltered, area)

        print(
            f"{question['id']:<4}"
            f"{area:<18}"
            f"{('HIT' if u_hit_5 else 'MISS'):>12}"
            f"{('HIT' if f_hit_5 else 'MISS'):>10}"
            f"{('HIT' if w_hit_5 else 'MISS'):>8}"
            f"{off_topic:>7}/5"
            f"{changed:>9}"
        )

        rows.append(
            {
                "question_id": question["id"],
                "question": question["question"],
                "product_area": area,
                "unfiltered_chunk_ids": chunk_ids(unfiltered),
                "filtered_chunk_ids": chunk_ids(filtered),
                "wrong_filter_chunk_ids": chunk_ids(wrong),
                "unfiltered_hit@3": u_hit_3,
                "unfiltered_hit@5": u_hit_5,
                "filtered_hit@3": f_hit_3,
                "filtered_hit@5": f_hit_5,
                "wrong_filter_hit@5": w_hit_5,
                "off_topic_in_unfiltered_top5": off_topic,
                "chunks_changed_by_filter": changed,
                "unfiltered_top1_distance": round(
                    unfiltered[0]["distance"], 4
                ),
                "filtered_top1_distance": round(
                    filtered[0]["distance"], 4
                ),
            }
        )

    summary = {
        "unfiltered_hit@3": percent(unfiltered_hits_3),
        "unfiltered_hit@5": percent(unfiltered_hits_5),
        "filtered_hit@3": percent(filtered_hits_3),
        "filtered_hit@5": percent(filtered_hits_5),
        "wrong_filter_hit@5": percent(wrong_hits_5),
        "unfiltered_mrr": sum(unfiltered_mrr) / len(unfiltered_mrr),
        "filtered_mrr": sum(filtered_mrr) / len(filtered_mrr),
        "total_off_topic_unfiltered": sum(
            row["off_topic_in_unfiltered_top5"] for row in rows
        ),
        "total_chunks_changed": sum(
            row["chunks_changed_by_filter"] for row in rows
        ),
    }

    print("\n" + "-" * 78)
    print("SUMMARY")
    print("-" * 78)

    print(
        f"  chunk_hit@3   unfiltered {summary['unfiltered_hit@3']:.0f}%"
        f"   ->  filtered {summary['filtered_hit@3']:.0f}%"
    )

    print(
        f"  chunk_hit@5   unfiltered {summary['unfiltered_hit@5']:.0f}%"
        f"   ->  filtered {summary['filtered_hit@5']:.0f}%"
    )

    print(
        f"  MRR           unfiltered {summary['unfiltered_mrr']:.3f}"
        f"  ->  filtered {summary['filtered_mrr']:.3f}"
    )

    print(
        f"  wrong filter  chunk_hit@5 "
        f"{summary['wrong_filter_hit@5']:.0f}%  "
        f"(expected 0% - the answer is not in that product area)"
    )

    print(
        f"  off-topic chunks removed from top-5: "
        f"{summary['total_off_topic_unfiltered']}"
    )

    return rows, summary


# ============================================================
# 3. Ambiguous queries
# ============================================================

def compare_ambiguous(ambiguous):

    print("\n\n" + "=" * 78)
    print("3. AMBIGUOUS QUERIES - metadata supplies the missing scope")
    print("=" * 78)

    rows = []

    for question in ambiguous:

        where = build_where(**question["disambiguating_filter"])

        unfiltered = retrieve_chunks(
            question["question"],
            top_k=3,
            strategy=DEFAULT_STRATEGY
        )

        filtered = retrieve_chunks(
            question["question"],
            top_k=3,
            strategy=DEFAULT_STRATEGY,
            where=where
        )

        print(f"\n{question['id']}: \"{question['question']}\"")
        print(f"   filter: {question['disambiguating_filter']}")

        print(
            "   unfiltered articles: "
            + ", ".join(
                f"{chunk['article_id']}({chunk['product_area']})"
                for chunk in unfiltered
            )
        )

        print(
            "   filtered articles  : "
            + ", ".join(
                f"{chunk['article_id']}({chunk['product_area']})"
                for chunk in filtered
            )
        )

        rows.append(
            {
                "question_id": question["id"],
                "question": question["question"],
                "filter": question["disambiguating_filter"],
                "unfiltered": [
                    {
                        "chunk_id": chunk["chunk_id"],
                        "article_id": chunk["article_id"],
                        "product_area": chunk["product_area"],
                        "distance": round(chunk["distance"], 4),
                    }
                    for chunk in unfiltered
                ],
                "filtered": [
                    {
                        "chunk_id": chunk["chunk_id"],
                        "article_id": chunk["article_id"],
                        "product_area": chunk["product_area"],
                        "distance": round(chunk["distance"], 4),
                    }
                    for chunk in filtered
                ],
                "distinct_articles_unfiltered": len(
                    {chunk["article_id"] for chunk in unfiltered}
                ),
                "distinct_articles_filtered": len(
                    {chunk["article_id"] for chunk in filtered}
                ),
            }
        )

    return rows


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    questions = load_json(QUESTIONS_FILE)
    ambiguous = load_json(AMBIGUOUS_FILE)

    rows, summary = compare_filters(questions)
    ambiguous_rows = compare_ambiguous(ambiguous)

    with open(RESULTS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {
                "strategy": DEFAULT_STRATEGY,
                "top_k": TOP_K,
                "summary": summary,
                "per_question": rows,
                "ambiguous": ambiguous_rows,
            },
            file,
            indent=2
        )

    print(f"\n\nSaved -> {RESULTS_FILE}")
