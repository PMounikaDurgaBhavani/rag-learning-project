"""The workflow: the same task as three hard-coded steps. No loop.

    step 1  find_ticket(ticket_id)
    step 2  get_order(ticket.order_id)          — skipped when there is no
                                                  order id, which is itself a
                                                  fixed branch, not a retry
    step 3  lookup_refund_policy(tier, order_status)
    then    decide in code, and one model call to write the sentence

Same three tools, same model, same decoding settings and the same output
contract as ticket_agent. What differs is who chooses the order of the calls:
here the code does, always, and the model never sees a tool schema.

The branch at step 2 is a fixed if, taken on a value the tool returned. That
is the thing the verdict has to weigh: a path that varies by input is not by
itself an argument for a loop, only for a branch.

    python src/ticket_workflow.py CD-7007 --verbose
"""

import json
import os
import sys
import time

from agent_runtime import blank_result, chat, cost_usd
from support_tools import call_tool

REPLY_SYSTEM_PROMPT = (
    "You are a CloudDesk support agent. Write one sentence to the customer "
    "stating the decision that has already been made. Use only the facts given. "
    "Do not add policy, amounts or promises that are not in the facts. "
    "Reply with the sentence only."
)

PROMPT_VERSION = "workflow-v1.0"

# Same contract as the agent, decided here in code from the policy rule.
DECISION_BY_RULE = {
    "already_refunded": "already_refunded",
    "escalate_to_billing": "escalated",
    "escalate_order_not_found": "escalated",
    "refund_full_any_age": "refund_approved",
}


def decide(ticket, order, policy):
    """The whole policy decision, in code. No model involved."""

    rule = policy.get("rule")

    if rule in DECISION_BY_RULE:
        decision = DECISION_BY_RULE[rule]
        amount = order.get("amount") if decision == "refund_approved" else None
        return decision, amount, rule

    # refund_if_within_window: the one rule that needs arithmetic.
    days = order.get("days_since_charge")
    window = policy.get("window_days")
    if days is None or window is None:
        return "escalated", None, "missing_days_or_window"
    if days <= window:
        return "refund_approved", order.get("amount"), f"{days}d <= {window}d window"
    return "refund_denied", None, f"{days}d > {window}d window"


def resolve_ticket(ticket_id, verbose=False, log=None):
    """Three fixed steps, one reply call. Always returns the same shape as the agent."""

    lines = [] if log is None else log

    def record(line):
        lines.append(line)
        if verbose:
            print(line)

    started = time.perf_counter()
    meter = {"steps": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0,
             "tool_errors": 0, "calls_by_tool": {}}
    result = blank_result(ticket_id)

    def run_tool(name, arguments):
        outcome = call_tool(name, arguments)
        meter["steps"] += 1
        meter["tool_calls"] += 1
        meter["calls_by_tool"][name] = meter["calls_by_tool"].get(name, 0) + 1
        if isinstance(outcome, dict) and outcome.get("error"):
            meter["tool_errors"] += 1
        record(f"[workflow] step {meter['steps']}: {name}({json.dumps(arguments)}) -> "
               f"{json.dumps(outcome)[:160]}")
        return outcome

    # ---- step 1 -----------------------------------------------------------
    ticket = run_tool("find_ticket", {"ticket_id": ticket_id})
    if ticket.get("error"):
        result.update(decision="escalated", reply="Ticket could not be found; escalated.")
        return _finish(result, meter, started, lines, ticket_id)

    tier = ticket.get("tier")
    result["tier"] = tier

    # ---- step 2 -----------------------------------------------------------
    # No order id is not an error state to retry, it is the other branch.
    if ticket.get("order_id"):
        order = run_tool("get_order", {"order_id": ticket["order_id"]})
    else:
        order = {"order_status": "missing", "error": "order_id_missing"}
        record("[workflow] step 2 skipped: ticket carries no order id -> order_status=missing")

    order_status = order.get("order_status", "missing")

    # ---- step 3 -----------------------------------------------------------
    policy = run_tool("lookup_refund_policy", {"tier": tier, "order_status": order_status})

    decision, amount, reason = decide(ticket, order, policy)
    result["decision"] = decision
    result["refund_amount"] = round(float(amount), 2) if isinstance(amount, (int, float)) else None
    record(f"[workflow] decision={decision} amount={result['refund_amount']} ({reason})")

    # ---- the sentence -----------------------------------------------------
    facts = {
        "ticket_id": ticket_id,
        "tier": tier,
        "decision": decision,
        "refund_amount": result["refund_amount"],
        "order_status": order_status,
        "days_since_charge": order.get("days_since_charge"),
        "policy": policy.get("explanation"),
    }
    response = chat(
        [{"role": "system", "content": REPLY_SYSTEM_PROMPT},
         {"role": "user", "content": f"Facts:\n{json.dumps(facts, indent=2)}\n\nSentence:"}],
        max_new_tokens=90,
    )
    meter["input_tokens"] += response["input_tokens"]
    meter["output_tokens"] += response["output_tokens"]
    result["reply"] = response["text"].strip().strip('"')
    record(f"[workflow] reply call: in={response['input_tokens']} out={response['output_tokens']} "
           f"{response['latency_ms']:.0f}ms")

    return _finish(result, meter, started, lines, ticket_id)


def _finish(result, meter, started, lines, ticket_id):
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    return {
        "system": "workflow",
        "ticket_id": ticket_id,
        "result": result,
        "terminated_by": None,          # nothing to terminate: there is no loop
        "latency_ms": elapsed_ms,
        "tokens": meter["input_tokens"] + meter["output_tokens"],
        "cost_usd": cost_usd(meter["input_tokens"], meter["output_tokens"]),
        "meter": meter,
        "budgets": None,
        "prompt_version": PROMPT_VERSION,
        "log": lines,
    }


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    run = resolve_ticket(argv[0], verbose=True)
    print("\n" + json.dumps(run["result"], indent=2))
    print(f"\nsteps={run['meter']['steps']} tokens={run['tokens']} "
          f"cost=${run['cost_usd']:.6f} latency={run['latency_ms']:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
