"""Week 4 Task Set A on the command line — the tables results.md needs.

Prints the same numbers the Golden set view shows, from the same module,
so the report and the UI cannot disagree:

  * hit-rate@k before and after, on the same 12 questions
  * the R / G / Not-In-Corpus tally with one line of evidence per failure
  * p50 latency before and after
  * a per-question fixed / unfixed table
  * a shipping decision with the number behind it

Usage
-----
    python experiments/evaluate_golden_ab.py
    python experiments/evaluate_golden_ab.py --k 5
    python experiments/evaluate_golden_ab.py --markdown   # paste into results.md
"""

import argparse
import json
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)

import golden_eval
from vector_store import DEFAULT_STRATEGY

RESULTS_JSON = os.path.join(golden_eval.EVAL_DIR, "golden_ab_results.json")


def rank_cell(hit, rank, deep_rank):
    if hit:
        return f"#{rank}"
    return f"miss (#{deep_rank})" if deep_rank else "not retrieved"


def print_plain(report):
    base, imp = report["baseline"], report["improved"]

    print("=" * 100)
    print(f"GOLDEN SET — hit-rate@{report['k']} · {base['total']} questions "
          f"· strategy={report['strategy']}")
    print(f"ONE CHANGE: {report['single_change']}")
    print("=" * 100)

    print(f"\n  {'':<28}{'BASELINE':<28}{'IMPROVED':<28}DELTA")
    print("  " + "-" * 96)
    print(f"  {'retriever':<28}{base['label']:<28}{imp['label']:<28}")
    print(f"  {'hit-rate@' + str(report['k']):<28}"
          f"{str(base['hit_rate']) + '%  (' + str(base['hits']) + '/' + str(base['total']) + ')':<28}"
          f"{str(imp['hit_rate']) + '%  (' + str(imp['hits']) + '/' + str(imp['total']) + ')':<28}"
          f"{report['delta_hit_rate']:+.1f}pp")
    print(f"  {'p50 latency':<28}{str(base['p50_latency_ms']) + ' ms':<28}"
          f"{str(imp['p50_latency_ms']) + ' ms':<28}{report['delta_p50_ms']:+.2f} ms"
          + ("  (within noise)" if report["p50_within_noise"] else ""))
    print(f"  {'p95 latency':<28}{str(base['p95_latency_ms']) + ' ms':<28}"
          f"{str(imp['p95_latency_ms']) + ' ms':<28}")

    print(f"\n\n  BASELINE FAILURE TALLY: "
          f"R = {report['tally']['R']}, G = {report['tally']['G']}, "
          f"Not-In-Corpus = {report['tally']['Not-In-Corpus']}")
    print("  " + "-" * 96)
    for item in report["evidence"]:
        print(f"  [{item['label']}] {item['id']} — {item['question']}")
        print(f"        evidence: {item['evidence']}")

    print("\n\n  PER-QUESTION")
    print("  " + "-" * 96)
    print(f"  {'ID':<8}{'TYPE':<11}{'BASELINE':<18}{'IMPROVED':<18}{'LABEL':<16}STATUS")
    for row in report["comparison"]:
        kind = "exact" if row["has_exact_identifier"] else "semantic"
        print(
            f"  {row['id']:<8}{kind:<11}"
            f"{rank_cell(row['baseline_hit'], row['baseline_rank'], row['baseline_rank_in_candidates']):<18}"
            f"{rank_cell(row['improved_hit'], row['improved_rank'], row['improved_rank_in_candidates']):<18}"
            f"{(row['failure_label'] or '-'):<16}{row['status']}"
        )

    named = lambda ids: ", ".join(ids) if ids else "none"
    print(f"\n  Fixed          : {named(report['fixed'])}")
    print(f"  Left untouched : {named(report['unfixed'])}")
    print(f"  Regressed      : {named(report['regressed'])}")

    print("\n" + "=" * 100)
    print(f"  DECISION: {report['decision']}")
    print(f"  {report['rationale']}")
    print("=" * 100)


def print_markdown(report):
    base, imp = report["baseline"], report["improved"]
    named = lambda ids: ", ".join(f"`{i}`" for i in ids) if ids else "none"

    print(f"### Before → after (same {base['total']} questions, one variable changed)\n")
    print(f"**The one change:** {report['single_change']}\n")
    print("| Metric | Baseline | Improved | Delta |")
    print("|---|---|---|---|")
    print(f"| Retriever | {base['label']} | {imp['label']} | — |")
    print(f"| hit-rate@{report['k']} | {base['hit_rate']}% ({base['hits']}/{base['total']}) "
          f"| {imp['hit_rate']}% ({imp['hits']}/{imp['total']}) | {report['delta_hit_rate']:+.1f}pp |")
    noise = " (within noise)" if report["p50_within_noise"] else ""
    print(f"| p50 latency | {base['p50_latency_ms']} ms | {imp['p50_latency_ms']} ms "
          f"| {report['delta_p50_ms']:+.2f} ms{noise} |")
    print(f"| p95 latency | {base['p95_latency_ms']} ms | {imp['p95_latency_ms']} ms | — |")

    print(f"\n### Baseline failure tally\n")
    print(f"**R = {report['tally']['R']} · G = {report['tally']['G']} · "
          f"Not-In-Corpus = {report['tally']['Not-In-Corpus']}**\n")
    print("| ID | Label | Evidence |")
    print("|---|---|---|")
    for item in report["evidence"]:
        print(f"| {item['id']} | {item['label']} | {item['evidence']} |")

    print("\n### Per-question fixed / unfixed\n")
    print(f"| ID | Question | Known-correct chunk | Type | Baseline | Improved | Label | Status |")
    print("|---|---|---|---|---|---|---|---|")
    for row in report["comparison"]:
        kind = "exact" if row["has_exact_identifier"] else "semantic"
        print(
            f"| {row['id']} | {row['question']} | `{row['expected_chunk_id']}` | {kind} "
            f"| {rank_cell(row['baseline_hit'], row['baseline_rank'], row['baseline_rank_in_candidates'])} "
            f"| {rank_cell(row['improved_hit'], row['improved_rank'], row['improved_rank_in_candidates'])} "
            f"| {row['failure_label'] or '—'} | **{row['status']}** |"
        )

    print(f"\n- **Fixed:** {named(report['fixed'])}")
    print(f"- **Left untouched:** {named(report['unfixed'])}")
    print(f"- **Regressed:** {named(report['regressed'])}")
    print(f"\n### Shipping decision\n\n**{report['decision']}** — {report['rationale']}")


def main():
    parser = argparse.ArgumentParser(
        description="hit-rate@k before/after on the golden set, one change."
    )
    parser.add_argument("--k", type=int, default=golden_eval.DEFAULT_K)
    parser.add_argument("--strategy", default=DEFAULT_STRATEGY)
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="emit markdown tables ready to paste into results.md"
    )
    args = parser.parse_args()

    report = golden_eval.evaluate(k=args.k, strategy=args.strategy)

    if args.markdown:
        print_markdown(report)
    else:
        print_plain(report)

    with open(RESULTS_JSON, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    if not args.markdown:
        print(f"\nWritten to {RESULTS_JSON}")


if __name__ == "__main__":
    main()
