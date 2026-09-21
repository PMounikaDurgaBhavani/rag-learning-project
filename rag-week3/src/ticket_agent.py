"""The agent: a tool-calling loop over the three refund-chase tools.

The model decides which tool to call and when it has enough to answer. The
loop's only jobs are to run the tools it asks for, keep the transcript, meter
what that costs, and stop.

Stopping is the part that has to be real. All four budgets — laps, tokens,
cost, wall clock — are checked together before every lap by
agent_runtime.Budgets.exceeded, and a run that hits one terminates with a
recorded reason and whatever facts it had gathered, rather than spinning.

    python -m ticket_agent CD-7001            # from src/
    python src/ticket_agent.py CD-7007 --verbose
"""

import json
import os
import sys
import time

from agent_runtime import (
    Budgets,
    blank_result,
    chat,
    cost_usd,
    extract_json,
    normalise_result,
    parse_tool_calls,
)
from support_tools import TOOL_NAMES, TOOL_SCHEMAS, call_tool

SYSTEM_PROMPT = (
    "You are a CloudDesk support agent resolving a refund-chase ticket.\n"
    "Work only from tool results. Never invent an amount, a tier, a date or a policy.\n"
    "Call one tool at a time. Typical order: find the ticket, then get the order it "
    "refers to, then look up the policy for that tier and order status.\n"
    "When you have the facts, reply with ONLY this JSON object and nothing else:\n"
    '{"ticket_id": "...", "decision": "refund_approved|refund_denied|already_refunded|escalated", '
    '"refund_amount": number or null, "tier": "standard|priority", "reply": "one sentence to the customer"}\n'
    "refund_amount is the order amount when a refund is approved, otherwise null."
)

PROMPT_VERSION = "agent-v1.0"


def _user_prompt(ticket_id):
    return f"Resolve ticket {ticket_id}."


def resolve_ticket(ticket_id, budgets=None, verbose=False, log=None):
    """Run the loop for one ticket. Always returns a result and a full meter."""

    budgets = budgets or Budgets()
    lines = [] if log is None else log

    def record(line):
        lines.append(line)
        if verbose:
            print(line)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(ticket_id)},
    ]

    started = time.perf_counter()
    meter = {"iterations": 0, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0,
             "tool_errors": 0, "wrong_tool_calls": 0, "calls_by_tool": {}}
    result = blank_result(ticket_id)
    terminated_by = None
    seen_tools = set()

    record(f"[agent] ticket={ticket_id} budgets={budgets.as_dict()}")

    while True:
        tokens = meter["input_tokens"] + meter["output_tokens"]
        spent = cost_usd(meter["input_tokens"], meter["output_tokens"])
        elapsed = time.perf_counter() - started

        # One place asks all four questions, before any further spend.
        limit, detail = budgets.exceeded(meter["iterations"], tokens, spent, elapsed)
        if limit:
            terminated_by = limit
            record(f"[agent] BUDGET {limit} hit ({detail}) after {meter['iterations']} "
                   f"iterations, {tokens} tokens, ${spent:.6f}, {elapsed:.1f}s "
                   f"— terminating cleanly")
            break

        meter["iterations"] += 1
        response = chat(messages, tools=TOOL_SCHEMAS)
        meter["input_tokens"] += response["input_tokens"]
        meter["output_tokens"] += response["output_tokens"]
        record(f"[agent] lap {meter['iterations']}: in={response['input_tokens']} "
               f"out={response['output_tokens']} {response['latency_ms']:.0f}ms")

        calls = parse_tool_calls(response["text"])

        if not calls:
            payload = extract_json(response["text"])
            if isinstance(payload, dict) and "decision" in payload:
                result = normalise_result(payload, ticket_id)
                record(f"[agent] final: decision={result['decision']} "
                       f"amount={result['refund_amount']}")
                break

            # No tool call and no answer: say so once and let it try again
            # rather than accepting an empty result.
            record(f"[agent] unparseable reply: {response['text'][:120]!r}")
            messages.append({"role": "assistant", "content": response["text"]})
            messages.append({"role": "user", "content":
                             "That was neither a tool call nor the JSON object. "
                             "Call a tool, or reply with only the JSON object."})
            continue

        messages.append({"role": "assistant", "content": response["text"]})

        for call in calls:
            name, arguments = call["name"], call["arguments"]
            outcome = call_tool(name, arguments)

            meter["tool_calls"] += 1
            meter["calls_by_tool"][name] = meter["calls_by_tool"].get(name, 0) + 1
            if isinstance(outcome, dict) and outcome.get("error"):
                meter["tool_errors"] += 1
            # A tool called a second time with no new information, or a tool
            # that does not exist, is the thrash that sharpening a description
            # is supposed to remove.
            if name not in TOOL_NAMES or name in seen_tools:
                meter["wrong_tool_calls"] += 1
            seen_tools.add(name)

            record(f"[agent]   tool {name}({json.dumps(arguments)}) -> "
                   f"{json.dumps(outcome)[:160]}")
            messages.append({"role": "tool", "name": name,
                             "content": json.dumps(outcome)})

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    tokens = meter["input_tokens"] + meter["output_tokens"]

    return {
        "system": "agent",
        "ticket_id": ticket_id,
        "result": result,
        "terminated_by": terminated_by,
        "latency_ms": elapsed_ms,
        "tokens": tokens,
        "cost_usd": cost_usd(meter["input_tokens"], meter["output_tokens"]),
        "meter": meter,
        "budgets": budgets.as_dict(),
        "prompt_version": PROMPT_VERSION,
        "log": lines,
    }


def main(argv):
    if not argv:
        print(__doc__)
        return 1

    ticket_id = argv[0]
    overrides = {}
    for flag, key in (("--max-iterations", "max_iterations"), ("--max-tokens", "max_tokens"),
                      ("--max-cost", "max_cost_usd"), ("--max-seconds", "max_wall_seconds")):
        if flag in argv:
            value = argv[argv.index(flag) + 1]
            overrides[key] = float(value) if "." in value or key in ("max_cost_usd", "max_wall_seconds") else int(value)

    run = resolve_ticket(ticket_id, budgets=Budgets(**overrides), verbose=True)
    print("\n" + json.dumps(run["result"], indent=2))
    print(f"\niterations={run['meter']['iterations']} tokens={run['tokens']} "
          f"cost=${run['cost_usd']:.6f} latency={run['latency_ms']:.0f}ms "
          f"terminated_by={run['terminated_by']}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main(sys.argv[1:]))
