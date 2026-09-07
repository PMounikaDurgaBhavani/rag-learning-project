"""Week 4 Task Set A: hit-rate@3 on the golden set, before and after ONE change.

The assignment turns on a single discipline — exactly one variable moves
between the two runs. Both arms here share the same 12 questions, the same
chunking strategy, the same k, and the same index. The only difference is
how candidates are ranked:

    baseline   dense cosine only                     (Week 3 retriever)
    improved   dense + BM25 fused with RRF, k=60     (the one change)

No reranker in the improved arm. Fusing in a cross-encoder as well would
make the delta unattributable, which is the failure mode the brief calls
out by name.

Latency is measured around the retrieval call alone, for the same reason:
the generator is identical in both arms, so including it would price a
change that was never made.
"""

import json
import os
import statistics
import time

from retriever import retrieve_chunks
from bm25_retriever import retrieve_bm25
from hybrid_retriever import reciprocal_rank_fusion
from vector_store import DEFAULT_STRATEGY

EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evaluation")
)

GOLDEN_SET_PATH = os.path.join(EVAL_DIR, "golden_set.jsonl")

# hit-rate@3 is the metric the brief asks for.
DEFAULT_K = 3

# RRF constant fixed by the brief.
RRF_K = 60

# How deep each retriever goes before fusion. Both arms see the same
# candidate pool so the comparison stays about ranking, not recall.
CANDIDATE_K = 25

ARMS = ("baseline", "improved")

ARM_LABELS = {
    "baseline": "Dense only (cosine)",
    "improved": "Dense + BM25 · RRF k=60",
}


def load_golden_set(path=GOLDEN_SET_PATH):
    """The 12 golden questions, each with its known-correct chunk_id."""
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _retrieve(arm, query, strategy, candidate_k=CANDIDATE_K):
    """Run one arm and return its ranked chunk list."""

    if arm == "baseline":
        return retrieve_chunks(query, top_k=candidate_k, strategy=strategy)

    dense = retrieve_chunks(query, top_k=candidate_k, strategy=strategy)
    sparse = retrieve_bm25(query, top_k=candidate_k, strategy=strategy)
    return reciprocal_rank_fusion(dense, sparse, k_constant=RRF_K)


# Each question is timed this many times per arm; the median of the
# repeats becomes that question's latency. One pass is dominated by
# scheduler noise on a corpus this small.
LATENCY_REPEATS = 5


def warmup(questions, strategy=DEFAULT_STRATEGY):
    """Load and exercise both indexes before anything is timed.

    Chroma opens its collection and the BM25 index is built on first use.
    Whichever arm runs first would otherwise absorb that one-off cost and
    measure slower than the arm that follows — reversing the true ordering,
    since fusion strictly does more work than dense alone. A single warmup
    query is not enough to settle it; the whole set is.
    """
    for arm in ARMS:
        for question in questions:
            _retrieve(arm, question["question"], strategy)


def _summarise_arm(arm, rows, k):
    latencies = [row["latency_ms"] for row in rows]
    hits = sum(1 for row in rows if row["hit"])
    ordered = sorted(latencies)

    return {
        "arm": arm,
        "label": ARM_LABELS[arm],
        "k": k,
        "hits": hits,
        "total": len(rows),
        "hit_rate": round(hits / len(rows) * 100.0, 1) if rows else 0.0,
        "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": (
            round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 2)
            if ordered else 0.0
        ),
        "questions": rows,
    }


def measure_arms(questions, k=DEFAULT_K, strategy=DEFAULT_STRATEGY):
    """Time both arms together, question by question.

    Running all of one arm and then all of the other makes the comparison
    hostage to whatever else the machine was doing in between — any drift
    in load shifts an entire arm and shows up as a latency delta that the
    retrieval change did not cause. Interleaving at the level of a single
    query, and alternating which arm goes first on each repeat, puts both
    arms under the same conditions.
    """

    warmup(questions, strategy=strategy)

    samples = {arm: {q["id"]: [] for q in questions} for arm in ARMS}
    ranked = {arm: {} for arm in ARMS}

    for repeat in range(LATENCY_REPEATS):
        # Alternate order so neither arm always pays for a cold cache line.
        order = ARMS if repeat % 2 == 0 else tuple(reversed(ARMS))

        for question in questions:
            for arm in order:
                started = time.perf_counter()
                retrieved = _retrieve(arm, question["question"], strategy)
                samples[arm][question["id"]].append(
                    (time.perf_counter() - started) * 1000.0
                )
                ranked[arm][question["id"]] = [
                    chunk["chunk_id"] for chunk in retrieved
                ]

    arms = {}

    for arm in ARMS:
        rows = []

        for question in questions:
            ranked_ids = ranked[arm][question["id"]]
            expected = question["expected_chunk_id"]

            # Rank over the whole candidate list, so a miss still shows how
            # far away the correct chunk sat rather than just "not in top k".
            full_rank = (
                ranked_ids.index(expected) + 1
                if expected in ranked_ids
                else None
            )
            hit = full_rank is not None and full_rank <= k

            rows.append({
                "id": question["id"],
                "question": question["question"],
                "expected_chunk_id": expected,
                "has_exact_identifier": question["has_exact_identifier"],
                "hit": hit,
                "rank": full_rank if hit else None,
                "rank_in_candidates": full_rank,
                "top_k_ids": ranked_ids[:k],
                "latency_ms": round(
                    statistics.median(samples[arm][question["id"]]), 2
                ),
            })

        arms[arm] = _summarise_arm(arm, rows, k)

    return arms


def classify(baseline_row, k=DEFAULT_K):
    """Label a baseline miss R / G / Not-In-Corpus.

    Only R and Not-In-Corpus can be decided from retrieval alone, and the
    brief is explicit that a wrong answer is not evidence of R. G is a
    claim about the generator misusing context that was present, so it is
    left for the inspection view rather than guessed at here.
    """
    if baseline_row["hit"]:
        return None, None

    if baseline_row["rank_in_candidates"] is None:
        return "Not-In-Corpus", (
            f"Expected chunk {baseline_row['expected_chunk_id']} did not "
            f"appear anywhere in the top {CANDIDATE_K} dense candidates."
        )

    return "R", (
        f"Expected chunk {baseline_row['expected_chunk_id']} ranked "
        f"#{baseline_row['rank_in_candidates']} under dense retrieval, "
        f"outside top-{k}. "
        f"Top-{k} returned: {', '.join(baseline_row['top_k_ids'])}."
    )


def evaluate(k=DEFAULT_K, strategy=DEFAULT_STRATEGY):
    """Run both arms over the same golden set and compare them."""

    questions = load_golden_set()

    arms = measure_arms(questions, k=k, strategy=strategy)
    baseline = arms["baseline"]
    improved = arms["improved"]

    comparison = []
    tally = {"R": 0, "G": 0, "Not-In-Corpus": 0}
    evidence = []

    for base_row, imp_row in zip(baseline["questions"], improved["questions"]):

        label, why = classify(base_row, k=k)
        if label:
            tally[label] += 1
            evidence.append({
                "id": base_row["id"],
                "question": base_row["question"],
                "label": label,
                "evidence": why,
            })

        if base_row["hit"] and imp_row["hit"]:
            status = "MAINTAINED"
        elif base_row["hit"] and not imp_row["hit"]:
            status = "REGRESSED"
        elif not base_row["hit"] and imp_row["hit"]:
            status = "FIXED"
        else:
            status = "UNFIXED"

        comparison.append({
            "id": base_row["id"],
            "question": base_row["question"],
            "expected_chunk_id": base_row["expected_chunk_id"],
            "has_exact_identifier": base_row["has_exact_identifier"],
            "baseline_hit": base_row["hit"],
            "baseline_rank": base_row["rank"],
            "baseline_rank_in_candidates": base_row["rank_in_candidates"],
            "baseline_top_k_ids": base_row["top_k_ids"],
            "baseline_latency_ms": base_row["latency_ms"],
            "improved_hit": imp_row["hit"],
            "improved_rank": imp_row["rank"],
            "improved_rank_in_candidates": imp_row["rank_in_candidates"],
            "improved_top_k_ids": imp_row["top_k_ids"],
            "improved_latency_ms": imp_row["latency_ms"],
            "failure_label": label,
            "evidence": why,
            "status": status,
        })

    fixed = [r["id"] for r in comparison if r["status"] == "FIXED"]
    unfixed = [r["id"] for r in comparison if r["status"] == "UNFIXED"]
    regressed = [r["id"] for r in comparison if r["status"] == "REGRESSED"]

    delta_hit = improved["hit_rate"] - baseline["hit_rate"]
    delta_p50 = improved["p50_latency_ms"] - baseline["p50_latency_ms"]

    # On a corpus this small the two arms are milliseconds apart and the
    # sign of the delta flips between runs. Calling a half-millisecond
    # swing a speed-up (or a cost) would be reading noise as signal, so
    # anything inside the band is reported as no measurable difference.
    noise_band = max(0.5, baseline["p50_latency_ms"] * 0.05)
    p50_within_noise = abs(delta_p50) <= noise_band

    if p50_within_noise:
        latency_phrase = (
            f"no measurable latency cost "
            f"({delta_p50:+.2f} ms is inside the ±{noise_band:.2f} ms noise band)"
        )
    else:
        latency_phrase = f"{delta_p50:+.2f} ms p50"

    # State the rule, then apply it, so the verdict is not a matter of taste.
    if delta_hit > 0 and not regressed:
        decision = "SHIP"
        rationale = (
            f"hit-rate@{k} rose {baseline['hit_rate']:.1f}% → "
            f"{improved['hit_rate']:.1f}% (+{delta_hit:.1f}pp) with no regressions, "
            f"for {latency_phrase}."
        )
    elif delta_hit > 0 and regressed:
        decision = "SHIP WITH CAVEAT"
        rationale = (
            f"hit-rate@{k} rose +{delta_hit:.1f}pp but {len(regressed)} "
            f"previously-passing question(s) regressed: {', '.join(regressed)}."
        )
    elif delta_hit == 0:
        decision = "DO NOT SHIP"
        rationale = (
            f"hit-rate@{k} did not move ({baseline['hit_rate']:.1f}%), "
            f"so the change buys nothing ({latency_phrase})."
        )
    else:
        decision = "DO NOT SHIP"
        rationale = (
            f"hit-rate@{k} fell {baseline['hit_rate']:.1f}% → "
            f"{improved['hit_rate']:.1f}% ({delta_hit:.1f}pp)."
        )

    return {
        "k": k,
        "strategy": strategy,
        "rrf_k": RRF_K,
        "candidate_k": CANDIDATE_K,
        "single_change": "Dense-only ranking replaced by dense + BM25 fused with RRF (k=60)",
        "baseline": baseline,
        "improved": improved,
        "comparison": comparison,
        "tally": tally,
        "evidence": evidence,
        "fixed": fixed,
        "unfixed": unfixed,
        "regressed": regressed,
        "delta_hit_rate": round(delta_hit, 1),
        "delta_p50_ms": round(delta_p50, 2),
        "p50_noise_band_ms": round(noise_band, 2),
        "p50_within_noise": p50_within_noise,
        "latency_repeats": LATENCY_REPEATS,
        "decision": decision,
        "rationale": rationale,
    }
