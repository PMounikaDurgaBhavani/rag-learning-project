"""Benchmark evaluating Dense vs BM25 vs Hybrid RRF vs Hybrid + Cross-Encoder Reranker.

Measures Hit@1, Hit@3, Hit@5, MRR, and Table Grounding across the question suite.
"""

import os
import sys
import json

# Add src to pythonpath
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from loader import load_articles
from metrics import is_gold_chunk, is_table_grounded, hit_at_k, reciprocal_rank, percent
from retriever import retrieve_chunks
from bm25_retriever import retrieve_bm25
from hybrid_retriever import retrieve_hybrid


def run_benchmark():
    questions_path = os.path.join(os.path.dirname(__file__), "..", "evaluation", "questions.json")
    with open(questions_path, "r", encoding="utf-8") as f:
        questions = json.load(f)

    table_questions = [q for q in questions if q.get("type") == "table"]

    methods = [
        ("Dense Only (MiniLM)", lambda q, k: retrieve_chunks(q, top_k=k)),
        ("BM25 Only (Lexical)", lambda q, k: retrieve_bm25(q, top_k=k)),
        ("Hybrid RRF (Dense+BM25)", lambda q, k: retrieve_hybrid(q, top_k=k, rerank=False)),
        ("Hybrid + Cross-Encoder", lambda q, k: retrieve_hybrid(q, top_k=k, rerank=True)),
    ]

    print("=" * 82)
    print("      HYBRID RETRIEVAL & RERANKING BENCHMARK EVALUATION (Week 4)")
    print("=" * 82)
    print(f"Total Questions: {len(questions)} (Standard: {len(questions)-len(table_questions)}, Table: {len(table_questions)})\n")

    header = f"{'Retrieval Method':<26} | {'Hit@1':<8} | {'Hit@3':<8} | {'Hit@5':<8} | {'MRR':<8} | {'Table Grounding':<15}"
    print(header)
    print("-" * 82)

    summary_data = []

    for name, retriever_fn in methods:
        hit1_list = []
        hit3_list = []
        hit5_list = []
        mrr_list = []
        table_list = []

        for q in questions:
            retrieved = retriever_fn(q["question"], 5)
            
            hit1_list.append(hit_at_k(retrieved, q, 1))
            hit3_list.append(hit_at_k(retrieved, q, 3))
            hit5_list.append(hit_at_k(retrieved, q, 5))
            mrr_list.append(reciprocal_rank(retrieved, q))

        for tq in table_questions:
            retrieved = retriever_fn(tq["question"], 5)
            table_list.append(hit_at_k(retrieved, tq, 5, predicate=is_table_grounded))

        h1 = percent(hit1_list)
        h3 = percent(hit3_list)
        h5 = percent(hit5_list)
        mrr = sum(mrr_list) / len(mrr_list) if mrr_list else 0.0
        tg = percent(table_list)

        print(f"{name:<26} | {h1:6.1f}% | {h3:6.1f}% | {h5:6.1f}% | {mrr:6.3f} | {tg:13.1f}%")
        summary_data.append({
            "method": name,
            "hit_at_1": round(h1, 2),
            "hit_at_3": round(h3, 2),
            "hit_at_5": round(h5, 2),
            "mrr": round(mrr, 4),
            "table_grounding": round(tg, 2)
        })

    print("=" * 82)

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), "..", "evaluation", "hybrid_reranking_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)
    print(f"\nResults saved to: {out_path}\n")


if __name__ == "__main__":
    run_benchmark()
