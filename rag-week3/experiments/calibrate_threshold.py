"""Pick the retrieval distance threshold used to refuse answers.

Prints the top-1 distance for every supported and unsupported
question, then reports the accuracy of every candidate threshold so
the chosen value is justified by data rather than guessed.
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

from retriever import retrieve_chunks
from vector_store import DEFAULT_STRATEGY


EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "evaluation")
)

RESULTS_FILE = os.path.join(EVAL_DIR, "threshold_results.json")


def load_json(name):
    with open(
        os.path.join(EVAL_DIR, name), "r", encoding="utf-8"
    ) as file:
        return json.load(file)


if __name__ == "__main__":

    supported = load_json("questions.json")
    unsupported = load_json("unsupported_questions.json")

    print("=" * 78)
    print("REFUSAL THRESHOLD CALIBRATION")
    print("=" * 78)

    records = []

    print("\nSUPPORTED (should be answered)")
    print("-" * 78)

    for question in supported:

        chunks = retrieve_chunks(
            question["question"],
            top_k=1,
            strategy=DEFAULT_STRATEGY
        )

        distance = chunks[0]["distance"]

        records.append(
            {
                "id": question["id"],
                "question": question["question"],
                "supported": True,
                "type": question["type"],
                "top1_distance": round(distance, 4),
                "top1_chunk_id": chunks[0]["chunk_id"],
            }
        )

        print(f"  {question['id']:<4} {distance:.4f}  {question['question'][:58]}")

    print("\nUNSUPPORTED (should be refused)")
    print("-" * 78)

    for question in unsupported:

        chunks = retrieve_chunks(
            question["question"],
            top_k=1,
            strategy=DEFAULT_STRATEGY
        )

        distance = chunks[0]["distance"]

        records.append(
            {
                "id": question["id"],
                "question": question["question"],
                "supported": False,
                "type": question["type"],
                "top1_distance": round(distance, 4),
                "top1_chunk_id": chunks[0]["chunk_id"],
            }
        )

        print(
            f"  {question['id']:<4} {distance:.4f}  "
            f"{question['question'][:48]:<50} [{question['type']}]"
        )

    # --------------------------------------------------------
    # Sweep candidate thresholds
    # --------------------------------------------------------

    print("\n" + "=" * 78)
    print("THRESHOLD SWEEP (answer when top1_distance <= threshold)")
    print("=" * 78)

    print(
        f"{'threshold':>10}"
        f"{'answered ok':>14}"
        f"{'wrongly refused':>18}"
        f"{'wrongly answered':>19}"
        f"{'accuracy':>11}"
    )

    print("-" * 78)

    sweep = []

    candidates = [round(0.5 + 0.05 * i, 2) for i in range(0, 15)]

    for threshold in candidates:

        correct_answer = sum(
            1
            for record in records
            if record["supported"]
            and record["top1_distance"] <= threshold
        )

        wrongly_refused = sum(
            1
            for record in records
            if record["supported"]
            and record["top1_distance"] > threshold
        )

        wrongly_answered = sum(
            1
            for record in records
            if not record["supported"]
            and record["top1_distance"] <= threshold
        )

        correct_refusal = sum(
            1
            for record in records
            if not record["supported"]
            and record["top1_distance"] > threshold
        )

        accuracy = (
            (correct_answer + correct_refusal)
            / len(records)
            * 100
        )

        sweep.append(
            {
                "threshold": threshold,
                "answered_correctly": correct_answer,
                "wrongly_refused": wrongly_refused,
                "wrongly_answered": wrongly_answered,
                "accuracy": accuracy,
            }
        )

        print(
            f"{threshold:>10.2f}"
            f"{correct_answer:>10}/{len(supported):<3}"
            f"{wrongly_refused:>18}"
            f"{wrongly_answered:>19}"
            f"{accuracy:>10.1f}%"
        )

    best = max(sweep, key=lambda s: (s["accuracy"], -s["threshold"]))

    print("\n" + "=" * 78)
    print(
        f"BEST THRESHOLD: {best['threshold']:.2f} "
        f"(accuracy {best['accuracy']:.1f}%)"
    )
    print("=" * 78)

    print(
        "\nAny question that survives the distance gate but is still "
        "unanswerable\nmust be caught by the grounding check in the "
        "generator."
    )

    with open(RESULTS_FILE, "w", encoding="utf-8") as file:
        json.dump(
            {
                "records": records,
                "sweep": sweep,
                "best_threshold": best["threshold"],
            },
            file,
            indent=2
        )

    print(f"\nSaved -> {RESULTS_FILE}")
