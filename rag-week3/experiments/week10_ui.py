"""Week 10 data for the UI's "Multi-agent benchmark" view, plus the live chat.

ui_artefacts() reads week10/ (manifest, results, comparison, race table,
hand-off log, failure case, verdict, report) and the Week 6 agreement the
judge carries. Nothing is computed from scratch: the view shows the files.

run_chat() is the interactive path. It runs ONE ticket through the chosen
arm in this server process, with the same model, retrieval and reply rules
as the benchmark, and saves the result under week10/runs/live/ (never over
the benchmark files). The server serialises it behind its model lock.

    run_chat("single", "How long is a reset link valid?", {"tier": "Standard"})
    run_chat("multi",  "...", ticket, inject_fault="ticket_specialist")   # HTTP 500 demo
"""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
W10 = ROOT / "week10"
W6 = ROOT / "week6"
RUNS = W10 / "runs"
LIVE = RUNS / "live"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

MODES = ("single", "multi")
SPECIALISTS = ("ticket_specialist", "knowledge_specialist")
TIERS = ("Standard", "Priority")

# Questions the help centre cannot answer: the "not found" path, end to end.
NOT_FOUND_EXAMPLES = [
    {"id": "NF1", "label": "Not in the help centre: Slack integration",
     "message": "Does CloudDesk integrate with Slack, and how do I turn it on?",
     "ticket": {"tier": "Standard"}},
    {"id": "NF2", "label": "Not in the help centre: data residency",
     "message": "Which country are my ticket attachments stored in, and can I choose a region?",
     "ticket": {"tier": "Priority"}},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(path):
    return path.read_text(encoding="utf-8") if path.exists() else None


def _json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _slim_rows(data):
    """Per-case rows without transcripts: enough for a table and a drawer."""
    if not data:
        return None
    rows = []
    for r in data.get("rows", []):
        rows.append({k: r.get(k) for k in (
            "case_id", "mode", "policy_backed", "regression", "ticket_id", "question", "answer",
            "refused", "refusal_reason", "not_found", "degraded", "status", "pass", "judge",
            "judge_reason", "assertions_pass", "assertions", "citation_correct", "cited_articles",
            "reference_articles", "latency_ms", "input_tokens", "output_tokens", "tokens", "cost_usd",
            "model_calls", "handoffs", "specialist_calls", "resynthesis", "context_resend_multiplier",
            "routing", "tasks", "handoff_records", "errors", "retries", "trace_id", "langfuse_trace_id")})
    return {"ran_at": data.get("ran_at"), "judged_at": data.get("judged_at"), "arm": data.get("arm"),
            "app_version": data.get("app_version"), "model": data.get("model"),
            "retrieval": data.get("retrieval"), "decoding": data.get("decoding"),
            "transport": data.get("transport"), "cold_start_ms": data.get("cold_start_ms"),
            "judge": data.get("judge"), "pass_rule": data.get("pass_rule"),
            "source_run": data.get("source_run"), "rows": rows}


def examples():
    manifest = _json(W10 / "cases_10.json")
    by_id = {}
    if (W6 / "eval_cases.jsonl").exists():
        for line in (W6 / "eval_cases.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                case = json.loads(line)
                by_id[case["id"]] = case
    out = []
    for c in (manifest or {}).get("cases", []):
        ticket = (by_id.get(c["id"]) or {}).get("ticket") or {}
        out.append({"id": c["id"], "label": f"{c['id']} · {c['mode']}"
                    + (" · policy" if c["policy_backed"] else "") + (" · regression" if c["regression"] else ""),
                    "message": c["message"],
                    "ticket": {k: ticket.get(k) for k in ("ticket_id", "tier", "charge_amount",
                                                          "days_since_charge", "duplicate_charge",
                                                          "refund_requested")}})
    return out + NOT_FOUND_EXAMPLES


def ui_artefacts():
    comparison = _json(W10 / "comparison.json")
    agreement = _json(W6 / "agreement.json") or {}
    return {
        "manifest": _json(W10 / "cases_10.json"),
        "results": {"single": _slim_rows(_json(W10 / "results_single.json")),
                    "multi": _slim_rows(_json(W10 / "results_multi.json"))},
        "comparison": {k: v for k, v in (comparison or {}).items() if k != "per_case"} if comparison else None,
        "per_case": (comparison or {}).get("per_case"),
        "race_table_md": _text(W10 / "race_table.md"),
        "handoffs_log": _text(W10 / "handoffs.log"),
        "failure_md": _text(W10 / "failure_case.md"),
        "failure": _json(W10 / "failure_case.json"),
        "verdict_md": _text(W10 / "verdict.md"),
        "report_md": _text(W10 / "REPORT.md"),
        "checklist_md": _text(W10 / "checklist.md"),
        "readme_md": _text(W10 / "README.md"),
        "test_output": _text(W10 / "test_output.txt"),
        "runs": sorted(p.name for p in RUNS.glob("*.json")) if RUNS.exists() else [],
        "live_runs": sorted(p.name for p in LIVE.glob("*.json")) if LIVE.exists() else [],
        "examples": examples(),
        "judge_agreement": {"before_pct": agreement.get("agreement_before"),
                            "after_pct": agreement.get("agreement_after"),
                            "held_out_pct": ((agreement.get("agreement_after_detail") or {}).get("held_out") or {}).get("pct")},
        "modes": list(MODES), "specialists": list(SPECIALISTS),
    }


# ---------------------------------------------------------------------------
# model state: loaded once per server process, never duplicated
# ---------------------------------------------------------------------------

def status():
    """Which local models this process has already loaded.

    Every model lives in one module-level cache (ticket_reply._PIPELINE,
    embeddings._MODEL, reranker._RERANKER), so the first request of a server
    session pays for loading them and later requests do not. The UI uses this
    to say "loading the model" only when that is actually what is happening.
    """
    import embeddings
    import reranker
    import ticket_reply
    return {
        "model": ticket_reply.TICKET_MODEL_NAME,
        "model_loaded": ticket_reply._PIPELINE is not None,
        "embedder_loaded": embeddings._MODEL is not None,
        "reranker_loaded": reranker._RERANKER is not None,
    }


def warm_models():
    """Load the drafter, the embedder, the BM25 index and the cross-encoder
    through their own caches, so the first question in the browser does not
    pay for them. Returns the seconds it took. Does nothing about memory
    pressure: a swapped-out model is slow whether it was loaded early or late."""
    import time
    from multi_agent import default_anchors, default_retriever
    from ticket_reply import get_ticket_pipeline
    started = time.perf_counter()
    generate = get_ticket_pipeline()
    generate([{"role": "user", "content": "ok"}], max_new_tokens=1, do_sample=False,
             return_full_text=False)
    chunks = default_retriever("warm up the retrievers", top_k=1)
    default_anchors("warm up the retrievers", chunks)
    return round(time.perf_counter() - started, 1)


# ---------------------------------------------------------------------------
# the live chat
# ---------------------------------------------------------------------------

def _number(value, name):
    if value in (None, "", False):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")


def build_ticket(message, ticket):
    ticket = ticket or {}
    tier = (ticket.get("tier") or "Standard").strip().title()
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    amount = _number(ticket.get("charge_amount"), "charge_amount")
    days = _number(ticket.get("days_since_charge"), "days_since_charge")
    if amount is not None and days is None:
        raise ValueError("days_since_charge is required when charge_amount is given")
    ticket_id = (ticket.get("ticket_id") or "").strip().upper()
    if not ticket_id:
        ticket_id = "CD-UI-" + hashlib.sha256(f"{message}|{now_iso()}".encode()).hexdigest()[:6].upper()
    return {"ticket_id": ticket_id, "tier": tier, "message": message,
            "charge_amount": amount, "days_since_charge": int(days) if days is not None else None,
            "duplicate_charge": bool(ticket.get("duplicate_charge")),
            "refund_requested": bool(ticket.get("refund_requested"))}


def _save(payload):
    LIVE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = LIVE / f"{payload['mode']}_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path.relative_to(ROOT).as_posix()


def run_chat(mode, message, ticket=None, inject_fault=None):
    """One question through one arm. Returns the UI payload (see week10 view)."""
    message = (message or "").strip()
    if not message:
        raise ValueError("message is empty")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if inject_fault and inject_fault not in SPECIALISTS:
        raise ValueError(f"inject_fault must be one of {SPECIALISTS}")
    if inject_fault and mode != "multi":
        raise ValueError("a specialist fault can only be injected into the multi-agent path")

    ticket = build_ticket(message, ticket)
    from agent_runtime import RATE_CARD, cost_usd
    cold_start = not status()["model_loaded"]       # this request will load the weights

    if mode == "single":
        from ticket_reply import draft_ticket_reply
        import time
        started = time.perf_counter()
        r = draft_ticket_reply(ticket, source="week10-ui-single", tags=["week10", "ui", "arm:single"])
        latency_ms = round((time.perf_counter() - started) * 1000.0, 1)
        answered = not r["refused"]
        inp, out = (r.get("input_tokens") or 0) if answered else 0, (r.get("output_tokens") or 0) if answered else 0
        retrieved = r.get("retrieved", [])
        payload = {
            "mode": "single", "ticket": ticket, "answer": r["answer"], "refused": r["refused"],
            "refusal_reason": r.get("refusal_reason"), "not_found": bool(r["refused"]), "degraded": False,
            "task_status": "COMPLETED", "errors": [], "retries": 0,
            "citations": [{"marker": m, "chunk_id": retrieved[m - 1]["chunk_id"],
                           "article_id": retrieved[m - 1].get("article_id"),
                           "section": retrieved[m - 1].get("section")}
                          for m in r.get("model_cited", []) if 0 < m <= len(retrieved)],
            "retrieved": retrieved, "best_distance": r.get("best_distance"),
            "lexical_anchors": r.get("lexical_anchors", []), "gate_bypass": r.get("gate_bypass"),
            "tasks": [], "handoffs": [], "routing": None, "specialists": {}, "resynthesis": [],
            "meter": {"model_calls": 1 if answered else 0, "input_tokens": inp, "output_tokens": out,
                      "tokens": inp + out, "cost_usd": round(cost_usd(inp, out), 6), "rate_card": RATE_CARD,
                      "handoffs": 0, "specialist_calls": 0, "tool_calls": 1, "resynthesis": 0,
                      "base_context_tokens": r.get("base_context_tokens"),
                      "context_resend_multiplier": None, "errors": 0, "retries": 0},
            "latency_ms": latency_ms, "trace_id": r.get("trace_id"),
            "langfuse_trace_id": r.get("langfuse_trace_id"), "transport": "in-process",
            "model": r.get("model_name"), "prompt_version": r.get("prompt_version"),
        }
    else:
        from multi_agent import HTTPTransport, Manager
        worker = None
        try:
            if inject_fault:
                from specialist_worker import SpecialistWorker
                worker = SpecialistWorker(fault={"specialist": inject_fault,
                                                 "ticket_id": ticket["ticket_id"]}).start()
                manager = Manager(transport=HTTPTransport(worker.url))
            else:
                manager = Manager()
            r = manager.handle(ticket, source="week10-ui-multi",
                               tags=["week10", "ui", "arm:multi"] + (["fault:" + inject_fault] if inject_fault else []))
        finally:
            if worker is not None:
                worker.stop()
        payload = {
            "mode": "multi", "ticket": ticket, "answer": r["answer"], "refused": r["refused"],
            "refusal_reason": r["refusal_reason"], "not_found": r["not_found"], "degraded": r["degraded"],
            "task_status": r["status"], "errors": r["errors"], "retries": r["meter"]["retries"],
            "citations": r["citations"], "retrieved": r["retrieved"],
            "best_distance": (r["specialists"].get("knowledge_specialist") or {}).get("best_distance"),
            "lexical_anchors": (r["specialists"].get("knowledge_specialist") or {}).get("lexical_anchors", []),
            "gate_bypass": None, "tasks": r["tasks"], "handoffs": r["handoffs"], "routing": r["routing"],
            "specialists": r["specialists"], "resynthesis": r["resynthesis"], "meter": r["meter"],
            "latency_ms": r["latency_ms"], "trace_id": r.get("trace_id"),
            "langfuse_trace_id": r.get("langfuse_trace_id"), "transport": r["transport"],
            "model": r.get("model_name"), "prompt_version": r.get("prompt_version"),
            "fault": ({"specialist": inject_fault, "ticket_id": ticket["ticket_id"],
                       "worker_requests": worker.requests} if worker is not None else None),
        }
    payload["ran_at"] = now_iso()
    payload["cold_start"] = cold_start
    payload["saved_to"] = _save(payload)
    return payload
