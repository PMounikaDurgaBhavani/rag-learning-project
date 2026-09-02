"""Failure Separation & Analysis Engine.

Categorizes system failures into:
1. Retrieval Failure: Target document/chunk not retrieved in Top-K.
2. Generation Failure: Target retrieved in Top-K, but LLM failed to answer correctly or hallucinated.
3. Distance False Refusal: Target chunk exists, but distance threshold triggered premature refusal.
4. Ungrounded Refusal: LLM answered, but grounding gate rejected due to lack of source word coverage.
5. Success / Correct Refusal.
"""

import json
import os
from typing import List, Dict, Any, Tuple

from generator import generate_answer
from retriever import retrieve_chunks
from hybrid_retriever import retrieve_hybrid
from metrics import is_gold_chunk, normalize


def load_json(filepath: str) -> List[Dict[str, Any]]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_question_failure(
    question: Dict[str, Any],
    top_k: int = 5,
    retrieval_mode: str = "hybrid",
    strategy: str = "markdown"
) -> Dict[str, Any]:
    """Analyze single question execution and classify any failures."""
    q_text = question["question"]
    expected_article = question.get("expected_article_id")
    answer_must_contain = question.get("answer_must_contain", [])

    # 1. Execute Retrieval
    if retrieval_mode == "dense":
        retrieved_chunks = retrieve_chunks(q_text, top_k=top_k, strategy=strategy)
    elif retrieval_mode == "hybrid":
        retrieved_chunks = retrieve_hybrid(q_text, top_k=top_k, strategy=strategy, rerank=False)
    elif retrieval_mode == "hybrid_rerank":
        retrieved_chunks = retrieve_hybrid(q_text, top_k=top_k, strategy=strategy, rerank=True)
    else:
        retrieved_chunks = retrieve_chunks(q_text, top_k=top_k, strategy=strategy)

    # Check if target gold chunk was retrieved
    gold_retrieved = any(is_gold_chunk(c, question) for c in retrieved_chunks)
    article_retrieved = any(c.get("article_id") == expected_article for c in retrieved_chunks)

    # 2. Execute Full Generation Pipeline
    gen_result = generate_answer(
        query=q_text,
        top_k=top_k,
        strategy=strategy
    )

    answer = gen_result.get("answer", "")
    refused = gen_result.get("refused", False)
    refusal_reason = gen_result.get("refusal_reason")
    sources = gen_result.get("sources", [])

    # 3. Classify Failure Mode
    classification = "UNKNOWN"
    diagnosis_detail = ""

    # Check correctness of content
    has_expected_content = all(
        normalize(must) in normalize(answer)
        for must in answer_must_contain
    ) if answer_must_contain else True

    if not gold_retrieved and not article_retrieved:
        classification = "RETRIEVAL_FAILURE"
        diagnosis_detail = f"Expected article {expected_article} was not found in Top-{top_k} retrieved chunks."
    elif refused:
        if "distance_gate" in str(refusal_reason):
            classification = "DISTANCE_GATE_FALSE_REFUSAL"
            diagnosis_detail = f"Chunk retrieved but distance exceeded threshold: {refusal_reason}"
        elif "ungrounded_answer" in str(refusal_reason):
            classification = "GENERATION_UNGROUNDED_REFUSAL"
            diagnosis_detail = "LLM generated an answer but it failed source coverage verification."
        else:
            classification = "GENERATION_MODEL_REFUSED"
            diagnosis_detail = f"LLM emitted refusal sentinel despite chunks being retrieved: {refusal_reason}"
    else:
        if has_expected_content and sources:
            classification = "SUCCESS"
            diagnosis_detail = f"Correctly answered and verified against {len(sources)} source citations."
        else:
            classification = "GENERATION_CONTENT_FAILURE"
            diagnosis_detail = "Answer was generated but missed required gold facts or correct citations."

    return {
        "question_id": question.get("id"),
        "question": q_text,
        "classification": classification,
        "gold_retrieved": gold_retrieved,
        "article_retrieved": article_retrieved,
        "refused": refused,
        "refusal_reason": refusal_reason,
        "answer": answer,
        "diagnosis_detail": diagnosis_detail,
        "retrieved_chunk_ids": [c["chunk_id"] for c in retrieved_chunks],
        "citations": [s["chunk_id"] for s in sources],
    }


def run_failure_analysis_report(
    questions_file: str = "evaluation/questions.json",
    retrieval_mode: str = "hybrid"
) -> Dict[str, Any]:
    """Run failure separation across the evaluation set and print summary report."""
    questions = load_json(questions_file)
    results = [
        evaluate_question_failure(q, retrieval_mode=retrieval_mode)
        for q in questions
    ]

    breakdown = {}
    for r in results:
        cat = r["classification"]
        breakdown[cat] = breakdown.get(cat, 0) + 1

    print("\n" + "=" * 75)
    print(f"FAILURE SEPARATION REPORT (Mode: {retrieval_mode.upper()})")
    print("=" * 75)
    print(f"Total Questions Evaluated: {len(questions)}\n")

    for cat, count in breakdown.items():
        pct = (count / len(questions)) * 100
        print(f"  * {cat:<32} : {count:2d} ({pct:5.1f}%)")

    print("\nDetailed Question Breakdown:")
    print("-" * 75)
    for r in results:
        status_sym = "[OK]" if r["classification"] == "SUCCESS" else "[FAIL]"
        print(f"{status_sym} {r['question_id']}: {r['question'][:50]}...")
        print(f"     Status: {r['classification']} | Gold Retrieved: {r['gold_retrieved']}")
        print(f"     Diagnosis: {r['diagnosis_detail']}")

    return {"summary": breakdown, "details": results}


if __name__ == "__main__":
    run_failure_analysis_report()
