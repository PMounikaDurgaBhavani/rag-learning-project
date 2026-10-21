# Verdict

**Keep the single agent; take one tool from the multi-agent path.** Ten Week 6 tickets, same model, retrieval and judge.
Pass rate (assertions AND judge v2): single 2/10, multi 3/10; assertions alone 5/10 each; one case apart, n=10, no significance claimed.
Cost: multi used 2.9× the tokens (20,327 vs 7,008), 2.7× the cost ($0.000446 vs $0.000166 per case, stand-in rate card), 3.3 vs 0.9 model calls, 2.8 hand-offs per case.
Latency: p50 equal (80 s), tail doubled (216 s vs 104 s); latency on this laptop is noisy, tokens are not.
What won for multi: the ticket specialist's code-level refund determination plus the manager's conflict check rewrote a wrong draft on T21.
What lost: a second paraphrase hop dropped steps, leaked a source header or hit the token limit on plain how-to tickets (T11, T18, R04).
Failure: a specialist answering HTTP 500 was retried once, marked FAILED, and the reply degraded to a routed hand-off (API 503), never an invented refund.
Largest hand-off: the T21 synthesis, 2,401 tokens; mean context re-send 38.8× the ticket.
Bolt the policy determination and the conflict check onto the single agent; delegate only facts the model cannot derive from the passages it already reads.
