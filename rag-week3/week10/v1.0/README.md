# v1.0 — the first full run, kept as recorded

`src/multi_agent.py` at PROMPT_VERSION `week10-multi-v1.0` treated knowledge-specialist
findings that carried no `[n]` marker as "no evidence", and the manager refused the
ticket. The run showed the 1.5B model writing correct, passage-grounded findings and
omitting the marker on T11, R04 and T18, so the retrieval gate (which had passed, best
distances 0.53–0.63) was overruled by a formatting heuristic. v1.1 lets the gate decide
and records whether the findings cite; the multi arm was re-run and the top-level
`week10/` files are v1.1. The single-agent code did not change; `results_single.json`
here is the same run judged in the same session.

Multi arm, v1.0: 3/10 pass (judge 4/10, assertions 7/10), 4 refused of which 3 were the
false "no evidence"; p50 98.7 s, 16,815 tokens, 30 model calls, 28 hand-offs.
Single arm: 2/10 pass (judge 3/10, assertions 5/10), 1 refused; p50 79.1 s, 7,008 tokens.
