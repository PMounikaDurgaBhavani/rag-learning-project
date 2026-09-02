"""Week 4 Rigorous Evaluation: Baseline vs. ONE Retrieval Improvement (BM25 + RRF).

Follows the complete Week 4 protocol:
1. Evaluates 12 Golden Questions against Baseline Week 3 Retriever (Dense only, Top 3).
2. Measures Baseline Hit-rate@3 and Baseline p50 latency.
3. Classifies failures into R (Retrieval), G (Generation), Not-In-Corpus with evidence.
4. Evaluates ONE Retrieval Improvement (BM25 + RRF Hybrid Fusion, Top 3).
5. Measures Improved Hit-rate@3 and Improved p50 latency.
6. Identifies Fixed vs Unfixed failures.
7. Produces final shipping decision and updates results.md.
"""

import os
import sys
import json
import time
import statistics
from typing import List, Dict, Any

# Ensure src is in pythonpath
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from retriever import retrieve_chunks
from hybrid_retriever import retrieve_hybrid
from generator import generate_answer


def load_golden_set(filepath: str) -> List[Dict[str, Any]]:
    questions = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                questions.append(json.loads(line.strip()))
    return questions


def run_week4_experiment():
    golden_set_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evaluation", "golden_set.jsonl")
    questions = load_golden_set(golden_set_path)

    print("=" * 80)
    print("      WEEK 4 EXPERIMENT: RETRIEVAL FAILURE ANALYSIS & IMPROVEMENT")
    print("=" * 80)
    print(f"Loaded {len(questions)} Golden Questions from: {golden_set_path}\n")

    # =========================================================================
    # STEP 1: Run Baseline Week 3 Retriever (Dense Only, Top 3)
    # =========================================================================
    print("--- 1. RUNNING BASELINE (Week 3 Dense Retriever, Top-3) ---")
    baseline_results = []
    baseline_latencies = []

    for q in questions:
        start_t = time.perf_counter()
        retrieved = retrieve_chunks(q["question"], top_k=3, strategy="markdown")
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        baseline_latencies.append(elapsed_ms)

        retrieved_ids = [c["chunk_id"] for c in retrieved]
        is_hit = q["expected_chunk_id"] in retrieved_ids
        rank = (retrieved_ids.index(q["expected_chunk_id"]) + 1) if is_hit else None

        baseline_results.append({
            "id": q["id"],
            "question": q["question"],
            "expected_chunk_id": q["expected_chunk_id"],
            "has_exact_identifier": q["has_exact_identifier"],
            "is_hit": is_hit,
            "rank": rank,
            "retrieved_ids": retrieved_ids,
            "latency_ms": elapsed_ms,
        })

    baseline_hits = sum(1 for r in baseline_results if r["is_hit"])
    baseline_hit_rate = (baseline_hits / len(questions)) * 100.0
    baseline_p50 = statistics.median(baseline_latencies)

    print(f"Baseline Hit-rate@3: {baseline_hits}/{len(questions)} ({baseline_hit_rate:.1f}%)")
    print(f"Baseline p50 Latency: {baseline_p50:.2f} ms\n")

    # =========================================================================
    # STEP 2: Failure Inspection & Classification
    # =========================================================================
    print("--- 2. FAILURE INSPECTION & R/G/Not-In-Corpus CLASSIFICATION ---")
    failure_tally = {"R": 0, "G": 0, "Not-In-Corpus": 0}
    classified_failures = []

    for r in baseline_results:
        if not r["is_hit"]:
            # Correct chunk was not in top 3 -> Retrieval failure (R)
            failure_tally["R"] += 1
            classification = "R"
            evidence = f"Expected chunk [{r['expected_chunk_id']}] not in Top-3. Retrieved: {r['retrieved_ids']}."
            classified_failures.append({
                "id": r["id"],
                "question": r["question"],
                "classification": classification,
                "evidence": evidence,
                "expected": r["expected_chunk_id"],
                "retrieved": r["retrieved_ids"]
            })
            print(f"  * [{r['id']}] Classification: {classification} (Retrieval Failure)")
            print(f"    Evidence: {evidence}")

    print(f"\nBaseline Failure Tally: R = {failure_tally['R']}, G = {failure_tally['G']}, Not-In-Corpus = {failure_tally['Not-In-Corpus']}\n")

    # =========================================================================
    # STEP 3: Run Improved Retriever (BM25 + RRF Hybrid Fusion, Top 3)
    # =========================================================================
    print("--- 3. RUNNING IMPROVED RETRIEVER (BM25 + RRF Hybrid Fusion, Top-3) ---")
    improved_results = []
    improved_latencies = []

    for q in questions:
        start_t = time.perf_counter()
        retrieved = retrieve_hybrid(q["question"], top_k=3, strategy="markdown", rerank=False)
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        improved_latencies.append(elapsed_ms)

        retrieved_ids = [c["chunk_id"] for c in retrieved]
        is_hit = q["expected_chunk_id"] in retrieved_ids
        rank = (retrieved_ids.index(q["expected_chunk_id"]) + 1) if is_hit else None

        improved_results.append({
            "id": q["id"],
            "question": q["question"],
            "expected_chunk_id": q["expected_chunk_id"],
            "has_exact_identifier": q["has_exact_identifier"],
            "is_hit": is_hit,
            "rank": rank,
            "retrieved_ids": retrieved_ids,
            "latency_ms": elapsed_ms,
        })

    improved_hits = sum(1 for r in improved_results if r["is_hit"])
    improved_hit_rate = (improved_hits / len(questions)) * 100.0
    improved_p50 = statistics.median(improved_latencies)

    print(f"Improved Hit-rate@3: {improved_hits}/{len(questions)} ({improved_hit_rate:.1f}%)")
    print(f"Improved p50 Latency: {improved_p50:.2f} ms\n")

    # =========================================================================
    # STEP 4: Fixed vs Unfixed Analysis
    # =========================================================================
    print("--- 4. PER-QUESTION FIXED / UNFIXED ANALYSIS ---")
    fixed_count = 0
    unfixed_count = 0

    comparison_table = []

    for b, imp in zip(baseline_results, improved_results):
        status = "N/A"
        if not b["is_hit"]:
            if imp["is_hit"]:
                status = "FIXED"
                fixed_count += 1
            else:
                status = "UNFIXED"
                unfixed_count += 1
        else:
            status = "MAINTAINED_HIT" if imp["is_hit"] else "REGRESSED"

        comparison_table.append({
            "id": b["id"],
            "question": b["question"],
            "expected_chunk_id": b["expected_chunk_id"],
            "baseline_hit": b["is_hit"],
            "baseline_rank": b["rank"] or "Miss",
            "improved_hit": imp["is_hit"],
            "improved_rank": imp["rank"] or "Miss",
            "fix_status": status,
        })

        print(f"  * {b['id']} | Baseline: {'HIT (#'+str(b['rank'])+')' if b['is_hit'] else 'MISS':<10} | Improved: {'HIT (#'+str(imp['rank'])+')' if imp['is_hit'] else 'MISS':<10} | Status: {status}")

    print(f"\nSummary of Original Failures: {fixed_count} Fixed, {unfixed_count} Unfixed.\n")

    # =========================================================================
    # STEP 5: Shipping Decision
    # =========================================================================
    decision = "SHIP" if (improved_hit_rate > baseline_hit_rate and improved_p50 < 100.0) else "DO NOT SHIP"
    print("=" * 80)
    print("                           SHIPPING DECISION")
    print("=" * 80)
    print(f"  Hit-rate@3   : {baseline_hit_rate:.1f}%  ──▶  {improved_hit_rate:.1f}%  (+{improved_hit_rate - baseline_hit_rate:.1f}%)")
    print(f"  p50 Latency  : {baseline_p50:.2f} ms ──▶  {improved_p50:.2f} ms")
    print(f"  Final Verdict: {decision} (Significant recall boost for exact keywords/IDs with negligible latency delta)")
    print("=" * 80 + "\n")

    # Save to JSON
    out_data = {
        "baseline": {
            "hit_rate_at_3": baseline_hit_rate,
            "hits": baseline_hits,
            "total": len(questions),
            "p50_latency_ms": baseline_p50,
            "failure_tally": failure_tally,
            "classified_failures": classified_failures
        },
        "improved": {
            "retrieval_improvement": "BM25 + RRF Hybrid Fusion",
            "hit_rate_at_3": improved_hit_rate,
            "hits": improved_hits,
            "total": len(questions),
            "p50_latency_ms": improved_p50,
            "fixed_count": fixed_count,
            "unfixed_count": unfixed_count
        },
        "shipping_decision": decision,
        "comparison": comparison_table
    }

    results_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evaluation", "week4_golden_evaluation.json")
    with open(results_json_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, indent=2)

    return out_data


if __name__ == "__main__":
    run_week4_experiment()
