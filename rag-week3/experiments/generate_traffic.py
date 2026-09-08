"""Bootstrap a trace log by putting varied questions through the app.

READ THIS BEFORE USING THE OUTPUT FOR FREQUENCIES
-------------------------------------------------
This is scaffolding, not traffic. It exists so the sampling and replay
tooling has something to work on today, and so you can see the trace
format before committing a week to collecting real logs.

Frequencies measured over these traces describe THIS SCRIPT'S question
mix, not your users. Whatever proportion of billing questions this file
contains is the proportion you will "discover" — which is the curated
sampling mistake the brief warns about, arrived at from the other end.

For a taxonomy you can defend, get real traces: use the app, put it in
front of the people who file the tickets, and let a week accumulate. Then
sample from that. Use this only to prove the pipeline works end to end.

The mix below is deliberately broad — answerable, unanswerable, vague,
misspelled, multi-part, out-of-scope, keyword-only — so that the traces
exercise different paths through the gates rather than one happy path.

Usage
-----
    python experiments/generate_traffic.py --runs 3
    python experiments/generate_traffic.py --runs 3 --mode dense
"""

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

EVAL_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "evaluation")
)


def load_json(name):
    with open(os.path.join(EVAL_DIR, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(name):
    path = os.path.join(EVAL_DIR, name)
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# Phrasings a real user produces that no curated evaluation set contains:
# typos, missing context, two questions at once, a bare keyword, politeness
# wrapped around the actual ask.
MESSY = [
    "hi, my upload keeps failing — any idea why?",
    "reset link expired agian, what now",
    "how do i chnage my payment method",
    "Can you tell me the storage limit and also how to increase it?",
    "billing",
    "why cant i see the upload button",
    "Account locked. Help.",
    "whats the max file size and which formats are allowed?",
    "I need to filter tickets but I don't know where that setting is",
    "notifications stopped working yesterday for the whole team",
    "Is there a way to export invoices?",
    "who do I contact about my organisation's storage?",
    "password reset not working pls help urgent",
    "what does error on upload mean",
    "How long do I have to wait after too many login attempts?",
    "can i upload a zip",
]


def build_question_pool():
    pool = []

    for item in load_json("questions.json"):
        pool.append((item["question"], "curated_supported"))

    for item in load_json("unsupported_questions.json"):
        pool.append((item["question"], "curated_unsupported"))

    for item in load_json("ambiguous_questions.json"):
        pool.append((item["question"], "curated_ambiguous"))

    for item in load_jsonl("golden_set.jsonl"):
        pool.append((item["question"], "golden"))

    for text in MESSY:
        pool.append((text, "messy"))

    return pool


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--runs", type=int, default=1,
                        help="passes over the question pool")
    parser.add_argument("--mode", default=None,
                        help="pin a retrieval mode (default: vary per query)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    from generator import generate_answer

    pool = build_question_pool()
    rng = random.Random(args.seed)

    modes = ("dense", "hybrid", "hybrid_rerank")

    print("!" * 74)
    print("Synthetic traffic. Frequencies over these traces describe this")
    print("script's question mix, NOT your users. See the module docstring.")
    print("!" * 74)
    print(f"\nPool: {len(pool)} questions × {args.runs} run(s) "
          f"= {len(pool) * args.runs} traces\n")

    total = 0
    started = time.perf_counter()

    for run in range(args.runs):
        order = pool[:]
        rng.shuffle(order)

        for question, kind in order:
            mode = args.mode or rng.choice(modes)
            # Vary top_k a little; a log where every call used identical
            # settings cannot show you settings-dependent failures.
            top_k = rng.choice([3, 5, 5, 7])

            try:
                result = generate_answer(
                    question,
                    top_k=top_k,
                    retrieval_mode=mode,
                    source=f"traffic:{kind}",
                )
                total += 1
                state = (
                    f"refused({result['refusal_reason']})"
                    if result["refused"] else "answered"
                )
                print(f"  [{total:>3}] {mode:<14} k={top_k} {state:<38} "
                      f"{question[:44]}")
            except Exception as error:
                print(f"  [!!!] {question[:50]} — {error}")

    elapsed = time.perf_counter() - started
    print(f"\n{total} traces in {elapsed:.1f}s "
          f"({elapsed / max(total, 1):.1f}s per answer)")
    print("Traces appended to traces/traces.jsonl")


if __name__ == "__main__":
    main()
