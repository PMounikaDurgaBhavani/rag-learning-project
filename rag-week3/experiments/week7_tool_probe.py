"""Does the third tool's description make the model pick the right tool?

The race cannot answer that on its own: if the agent stops calling tools after
the first one, a description bug never gets the chance to show. This probe asks
the one question directly, with the transcript fixed and only the description
changed.

Two decision points per ticket, each with one correct tool:

    after_ticket   find_ticket has returned. Correct: get_order, or
                   lookup_refund_policy when the ticket carries no order id.
    after_order    get_order has returned too. Correct: lookup_refund_policy.

Everything else is the same between variants — same model, same greedy
decoding, same transcript — so a difference in tool choice is the description.

    python experiments/week7_tool_probe.py --variant v1
    python experiments/week7_tool_probe.py --variant v2
    python experiments/week7_tool_probe.py --compare
"""

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

import support_tools
from agent_runtime import MODEL_NAME, chat, parse_tool_calls, warm_up
from ticket_agent import SYSTEM_PROMPT

W7 = ROOT / "week7"

# v1: the description as first written — it says "refund information for a
# customer's order", which is also what get_order returns.
DESCRIPTION_V1 = (
    "Get the refund information for a customer's order, including the "
    "refund window, whether the charge can be refunded and whether it "
    "needs to be escalated."
)

# v2: one job, named inputs, and an explicit statement of what it does NOT do.
DESCRIPTION_V2 = (
    "Return the refund rule for a (tier, order_status) pair: the refund window "
    "in days, whether the charge must be escalated, and the rule name. Reads no "
    "ticket and no order — call find_ticket for the tier and get_order for the "
    "order_status first, then pass those two values here."
)

VARIANTS = {"v1": DESCRIPTION_V1, "v2": DESCRIPTION_V2}


def schemas_with(description):
    schemas = copy.deepcopy(support_tools.TOOL_SCHEMAS)
    for schema in schemas:
        if schema["function"]["name"] == "lookup_refund_policy":
            schema["function"]["description"] = description
    return schemas


def tool_call_message(name, arguments):
    return {"role": "assistant",
            "content": f'<tool_call>\n{json.dumps({"name": name, "arguments": arguments})}\n</tool_call>'}


def decision_points(ticket_id):
    """Both transcript states for one ticket, with the tool that should follow."""
    ticket = support_tools.find_ticket(ticket_id)
    base = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Resolve ticket {ticket_id}."},
        tool_call_message("find_ticket", {"ticket_id": ticket_id}),
        {"role": "tool", "name": "find_ticket", "content": json.dumps(ticket)},
    ]

    points = [{
        "point": "after_ticket",
        "messages": base,
        "correct": "get_order" if ticket.get("order_id") else "lookup_refund_policy",
    }]

    order = (support_tools.get_order(ticket["order_id"]) if ticket.get("order_id")
             else {"order_status": "missing", "error": "order_id_missing"})
    points.append({
        "point": "after_order",
        "messages": base + [
            tool_call_message("get_order", {"order_id": ticket.get("order_id") or ""}),
            {"role": "tool", "name": "get_order", "content": json.dumps(order)},
        ],
        "correct": "lookup_refund_policy",
    })
    return points


def run_variant(variant):
    schemas = schemas_with(VARIANTS[variant])
    tickets = [json.loads(line)["ticket_id"]
               for line in (W7 / "race_set.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]

    warm_up()
    rows = []
    for ticket_id in tickets:
        for point in decision_points(ticket_id):
            response = chat(point["messages"], tools=schemas, max_new_tokens=80)
            calls = parse_tool_calls(response["text"])
            chosen = calls[0]["name"] if calls else None
            rows.append({
                "ticket_id": ticket_id,
                "point": point["point"],
                "correct_tool": point["correct"],
                "chosen_tool": chosen,
                "correct": chosen == point["correct"],
                "no_tool_call": chosen is None,
                "arguments": calls[0]["arguments"] if calls else None,
                "raw": response["text"][:200],
            })
            print(f"  {ticket_id} {point['point']:<13} want {point['correct']:<21} "
                  f"got {str(chosen):<21} {'ok' if chosen == point['correct'] else 'WRONG'}")

    correct = sum(1 for row in rows if row["correct"])
    summary = {
        "variant": variant,
        "description": VARIANTS[variant],
        "model": MODEL_NAME,
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decision_points": len(rows),
        "correct_tool_choices": correct,
        "correct_pct": round(100.0 * correct / len(rows), 1),
        "no_tool_call": sum(1 for row in rows if row["no_tool_call"]),
        "wrong_tool": sum(1 for row in rows if not row["correct"] and not row["no_tool_call"]),
        "rows": rows,
    }
    (W7 / f"tool_probe_{variant}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\n{variant}: {correct}/{len(rows)} correct tool choices "
          f"({summary['correct_pct']}%) · wrong tool {summary['wrong_tool']} · "
          f"no tool call {summary['no_tool_call']}")
    print(f"Wrote week7/tool_probe_{variant}.json")
    return summary


def compare():
    loaded = {}
    for variant in VARIANTS:
        path = W7 / f"tool_probe_{variant}.json"
        if path.exists():
            loaded[variant] = json.loads(path.read_text(encoding="utf-8"))
    if len(loaded) < 2:
        print("Run both variants first.")
        return 1

    print(f"{'variant':<8} {'correct tool':>14} {'wrong tool':>12} {'no tool call':>14}")
    for variant, data in loaded.items():
        correct = "{}/{}".format(data["correct_tool_choices"], data["decision_points"])
        print(f"{variant:<8} {correct:>14} {data['wrong_tool']:>12} "
              f"{data['no_tool_call']:>14}")

    if len(loaded) == 2:
        before, after = loaded["v1"], loaded["v2"]
        moved = [row["ticket_id"] + "/" + row["point"]
                 for row, later in zip(before["rows"], after["rows"])
                 if not row["correct"] and later["correct"]]
        broke = [row["ticket_id"] + "/" + row["point"]
                 for row, later in zip(before["rows"], after["rows"])
                 if row["correct"] and not later["correct"]]
        print(f"\nv1 -> v2: {before['correct_pct']}% -> {after['correct_pct']}% correct")
        print(f"  fixed by the sharper description: {', '.join(moved) or 'none'}")
        print(f"  broken by it:                    {', '.join(broke) or 'none'}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--variant", choices=sorted(VARIANTS))
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()

    if args.compare:
        return compare()
    if not args.variant:
        parser.error("pass --variant v1|v2 or --compare")
    run_variant(args.variant)
    return 0


if __name__ == "__main__":
    sys.exit(main())
