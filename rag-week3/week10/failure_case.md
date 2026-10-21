# Week 10 — failure case: the policy worker answers HTTP 500

Run 2026-10-21T12:59:53+00:00, build `7e44499`, transport HTTP via `src/specialist_worker.py`.

## What was broken, and only that

- Fault: `ticket_specialist` answers **HTTP 500** for ticket `CD-10407` (case T07) before the specialist runs. Every other request is served normally.
- Retry policy: `{"max_attempts": 2, "retry_on": [500, 502, 503, 504], "backoff_s": 0.2}`.

## What the manager did on the faulted case

- Question: refund??? charged twice $89 this month
- Worker log for that ticket: ticket_specialist -> 500 (injected), ticket_specialist -> 500 (injected), knowledge_specialist -> 200
- HTTP attempts to the faulted specialist: 2 (500, 500), then it stopped: retries are bounded at max_attempts=2.
- Task `task-42c071034f` (ticket_specialist): CREATED -> ASSIGNED -> RUNNING -> FAILED; error recorded: `{"kind": "http_error", "status": 500, "message": "ticket_specialist specialist returned HTTP 500 (2 attempts)", "attempts": 2}`.
- Root task status: **FAILED**, degraded=True, refused=True, reason `specialist_unavailable (ticket_specialist specialist returned HTTP 500 (2 attempts))`.
- Retries counted: 1. Hand-offs recorded: 4. Model calls: 2.

## The reply the customer would have received

```
[ESCALATED-PRIORITY]
CD-10407: Thanks for contacting CloudDesk support. I couldn't find the answer to this in our help centre, so I've passed your ticket to a specialist who will follow up.
```

This is `ticket_policy.routed_reply`: the ticket id, the escalation tag when the tier needs it, and a hand-off to a person. The manager did **not** write a refund decision without the specialist that owns the account facts, and the reply does not claim that anything was checked.

## Honest scoring

- Deterministic assertions on the degraded reply: FAIL: refund_amount_numeric.
- Judge: not run: the reply is the routed hand-off, which the judge criterion itself calls FAIL whenever the reference facts answer the question.
- So the failure costs this case its pass. The alternative, synthesising a refund answer from the help-centre passages alone, would have hidden the outage behind a confident reply; that is the misleading synthesis the design refuses.

## The control case, same worker, same fault switch

- Case T01 (`CD-10401`): status **COMPLETED**, degraded=False, errors=0, 3 model calls, 102.8 s.
- Worker log: knowledge_specialist -> 200

Raw record: `week10/failure_case.json`. Traces: faulted `959fdaaf7b4143cf`, control `c1c32e12275e4ad3`.
