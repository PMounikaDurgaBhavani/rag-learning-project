"""Evaluate grounded answering, citation correctness and refusals.

Runs three question sets through the full RAG pipeline:

  questions.json              8 known-answer questions - must answer,
                              correctly, with a citation that points at
                              a chunk that really contains the answer
  unsupported_questions.json  6 questions with no answer in the corpus
                              - must refuse
  ambiguous_questions.json    4 questions with no clear subject -
                              recorded for analysis
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

from generator import generate_answer, TOP_K
from retriever import retrieve_chunks
from vector_store import DEFAULT_STRATEGY
from metrics import is_gold_chunk, contains_all, percent


EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "evaluation")
)

RESULTS_FILE = os.path.join(EVAL_DIR, "generation_results.json")


def load_json(name):
    with open(
        os.path.join(EVAL_DIR, name), "r", encoding="utf-8"
    ) as file:
        return json.load(file)


# ============================================================
# Supported questions
# ============================================================

def evaluate_supported(questions):

    print("=" * 78)
    print("1. SUPPORTED QUESTIONS - must answer, with correct citations")
    print("=" * 78)

    rows = []

    answered = []
    correct = []
    citation_article_ok = []
    citation_chunk_ok = []

    for question in questions:

        # Retrieve separately so the cited chunk ids can be compared
        # against the chunks that genuinely contain the answer.
        retrieved = retrieve_chunks(
            question["question"],
            top_k=TOP_K,
            strategy=DEFAULT_STRATEGY
        )

        gold_chunk_ids = {
            chunk["chunk_id"]
            for chunk in retrieved
            if is_gold_chunk(chunk, question)
        }

        result = generate_answer(question["question"])

        was_answered = not result["refused"]

        is_correct = was_answered and contains_all(
            result["answer"],
            question["answer_must_contain"]
        )

        cited_ids = [
            source["chunk_id"]
            for source in result["sources"]
        ]

        cited_articles = [
            source["article_id"]
            for source in result["sources"]
        ]

        article_ok = (
            was_answered
            and bool(cited_articles)
            and all(
                article == question["expected_article_id"]
                for article in cited_articles
            )
        )

        chunk_ok = (
            was_answered
            and bool(cited_ids)
            and any(
                chunk_id in gold_chunk_ids
                for chunk_id in cited_ids
            )
        )

        answered.append(was_answered)
        correct.append(is_correct)

        if was_answered:
            citation_article_ok.append(article_ok)
            citation_chunk_ok.append(chunk_ok)

        print(
            f"\n{question['id']} [{question['type']}] "
            f"{question['question']}"
        )

        print(f"   answer   : {result['answer'][:120]}")

        print(
            f"   verdict  : "
            f"{'ANSWERED' if was_answered else 'REFUSED'}"
            f" | correct={is_correct}"
            f" | cite_article={article_ok}"
            f" | cite_gold_chunk={chunk_ok}"
        )

        if result["sources"]:
            print(
                "   cited    : "
                + ", ".join(
                    f"{s['chunk_id']}(cov {s['coverage']})"
                    for s in result["sources"]
                )
            )

        if gold_chunk_ids:
            print(f"   gold     : {sorted(gold_chunk_ids)}")
        else:
            print("   gold     : (no retrieved chunk contains the answer)")

        rows.append(
            {
                "id": question["id"],
                "question": question["question"],
                "type": question["type"],
                "answered": was_answered,
                "refusal_reason": result["refusal_reason"],
                "answer": result["answer"],
                "raw_answer": result["raw_answer"],
                "correct": is_correct,
                "cited_chunk_ids": cited_ids,
                "cited_articles": cited_articles,
                "gold_chunk_ids": sorted(gold_chunk_ids),
                "citation_article_correct": article_ok,
                "citation_points_to_gold_chunk": chunk_ok,
                "best_distance": result["best_distance"],
                "model_cited_markers": result["model_cited"],
            }
        )

    summary = {
        "answer_rate": percent(answered),
        "accuracy": percent(correct),
        "citation_article_correct": percent(citation_article_ok),
        "citation_points_to_gold_chunk": percent(citation_chunk_ok),
        "answered": sum(1 for value in answered if value),
        "total": len(questions),
    }

    print("\n" + "-" * 78)
    print(
        f"  answered            : {summary['answered']}/{summary['total']} "
        f"({summary['answer_rate']:.0f}%)"
    )
    print(f"  answer accuracy     : {summary['accuracy']:.0f}%")
    print(
        f"  citation -> correct article : "
        f"{summary['citation_article_correct']:.0f}%"
    )
    print(
        f"  citation -> gold chunk      : "
        f"{summary['citation_points_to_gold_chunk']:.0f}%"
    )

    return rows, summary


# ============================================================
# Unsupported questions
# ============================================================

def evaluate_unsupported(questions):

    print("\n\n" + "=" * 78)
    print("2. UNSUPPORTED QUESTIONS - must refuse")
    print("=" * 78)

    rows = []
    refused = []

    for question in questions:

        result = generate_answer(question["question"])

        refused.append(result["refused"])

        print(
            f"\n{question['id']} [{question['type']}] "
            f"{question['question']}"
        )

        print(f"   distance : {result['best_distance']}")

        print(
            f"   verdict  : "
            f"{'REFUSED' if result['refused'] else 'ANSWERED (FAILURE)'}"
            f"  -> {result['refusal_reason'] or result['answer'][:80]}"
        )

        rows.append(
            {
                "id": question["id"],
                "question": question["question"],
                "type": question["type"],
                "why_unsupported": question["why_unsupported"],
                "refused": result["refused"],
                "refusal_reason": result["refusal_reason"],
                "answer": result["answer"],
                "raw_answer": result["raw_answer"],
                "best_distance": result["best_distance"],
            }
        )

    by_gate = {}

    for row in rows:
        if row["refused"]:
            gate = (row["refusal_reason"] or "").split(" ")[0]
            by_gate[gate] = by_gate.get(gate, 0) + 1

    summary = {
        "refusal_rate": percent(refused),
        "refused": sum(1 for value in refused if value),
        "total": len(questions),
        "by_gate": by_gate,
    }

    print("\n" + "-" * 78)
    print(
        f"  refused : {summary['refused']}/{summary['total']} "
        f"({summary['refusal_rate']:.0f}%)"
    )

    for gate, count in sorted(by_gate.items()):
        print(f"     {gate:<34} {count}")

    return rows, summary


# ============================================================
# Ambiguous questions
# ============================================================

def evaluate_ambiguous(questions):

    print("\n\n" + "=" * 78)
    print("3. AMBIGUOUS QUESTIONS - behaviour recorded for analysis")
    print("=" * 78)

    rows = []

    for question in questions:

        result = generate_answer(question["question"])

        print(f"\n{question['id']} {question['question']}")
        print(f"   distance : {result['best_distance']}")
        print(
            f"   verdict  : "
            f"{'REFUSED' if result['refused'] else 'ANSWERED'}"
            f"  -> {result['refusal_reason'] or result['answer'][:90]}"
        )

        rows.append(
            {
                "id": question["id"],
                "question": question["question"],
                "note": question["note"],
                "refused": result["refused"],
                "refusal_reason": result["refusal_reason"],
                "answer": result["answer"],
                "best_distance": result["best_distance"],
                "cited_chunk_ids": [
                    source["chunk_id"]
                    for source in result["sources"]
                ],
            }
        )

    return rows


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    supported_rows, supported_summary = evaluate_supported(
        load_json("questions.json")
    )

    unsupported_rows, unsupported_summary = evaluate_unsupported(
        load_json("unsupported_questions.json")
    )

    ambiguous_rows = evaluate_ambiguous(
        load_json("ambiguous_questions.json")
    )

    with open(RESULTS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {
                "strategy": DEFAULT_STRATEGY,
                "top_k": TOP_K,
                "supported": {
                    "summary": supported_summary,
                    "rows": supported_rows,
                },
                "unsupported": {
                    "summary": unsupported_summary,
                    "rows": unsupported_rows,
                },
                "ambiguous": ambiguous_rows,
            },
            file,
            indent=2
        )

    print(f"\n\nSaved -> {RESULTS_FILE}")
