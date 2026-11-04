"""The /api/capstone/* routes. Thin: validation, locking and JSON shaping only.

Mounted by src/server.py; every model-backed action goes through the
server's one-run-at-a-time lock (409 when busy), as the earlier weeks' routes do.
"""

import json
import re
import statistics
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import observability as obs

from capstone import business_tools, conversation_store, escalation_queue, mcp_session, ticket_store
from capstone.config import (DEPLOYMENT_MODE, FRONTEND_DIST, INTEGRATION_LABELS, REPORTS_DIR, ROOT, WEEK12_DIR,
                             as_of)
from capstone.pii import mask_text

ACCESS_LOG = WEEK12_DIR / "logs" / "mcp_helpcentre_access.log"
PLANNERS = ("model", "code")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_file(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _text_file(path):
    path = Path(path)
    return path.read_text(encoding="utf-8") if path.exists() else None


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------

def health():
    import db
    try:
        import ticket_reply, agent_runtime, embeddings, reranker
        models = {"drafter_loaded": ticket_reply._PIPELINE is not None or agent_runtime._MODEL is not None,
                  "embedder_loaded": embeddings._MODEL is not None, "reranker_loaded": reranker._RERANKER is not None,
                  "agent_model": agent_runtime.MODEL_NAME}
    except Exception as error:
        models = {"error": str(error)}
    reachable = False
    try:
        reachable = db.server_reachable(db.DATABASE_URL)
    except Exception:
        pass
    return {"ok": reachable, "mode": DEPLOYMENT_MODE, "as_of": as_of().isoformat(),
            "store": db.safe_url(db.DATABASE_URL), "store_reachable": reachable,
            "documents": db.stats().get("documents") if reachable else None,
            "mcp": mcp_session.status(), "models": models, "frontend_built": (FRONTEND_DIST / "index.html").exists(),
            "integrations": INTEGRATION_LABELS, "tickets": ticket_store.counts()}


def deflection_rows(limit=500):
    found = obs.search(kind="deflection", limit=min(limit, obs.MAX_LIMIT), order="desc")
    return found.get("rows", [])


def dashboard():
    tickets = ticket_store.list_tickets()
    with_ai = [t for t in tickets if t.get("ai")]
    by_outcome = {"answered": 0, "escalated": 0}
    for t in with_ai:
        by_outcome[t["ai"].get("outcome", "escalated")] = by_outcome.get(t["ai"].get("outcome", "escalated"), 0) + 1
    deflected = [t for t in with_ai if t["ai"].get("outcome") == "answered" and t["status"] in ("answered", "resolved", "awaiting_approval")]
    rows = deflection_rows()
    latencies = [r["latency_ms"] for r in rows if r.get("latency_ms")]
    escalations = escalation_queue.list_escalations()
    reopen = _json_file(WEEK12_DIR / "reopen_eval.json")
    return {
        "as_of": as_of().isoformat(), "generated_at": now_iso(),
        "data_sources": {
            "tickets": INTEGRATION_LABELS["inbox"],
            "ai_runs": "traces/traces.jsonl records of kind 'deflection' (the Week 11 index)",
            "escalations": INTEGRATION_LABELS["escalation"],
            "reopen": "week12/reopen_eval.json (SIMULATED second-contact evaluation, not production data)"},
        "totals": {
            "tickets": len(tickets), "handled_by_ai": len(with_ai),
            "deflected": len(deflected), "escalated_by_ai": by_outcome.get("escalated", 0),
            "answered_by_ai": by_outcome.get("answered", 0),
            "deflection_rate_pct": round(100.0 * len(deflected) / len(with_ai), 1) if with_ai else None,
            "ai_runs_recorded": len(rows),
            "open_escalations": sum(1 for e in escalations if e.get("status") == "open"),
        },
        "latency": {
            "ai_mean_ms": round(statistics.mean(latencies), 1) if latencies else None,
            "ai_p50_ms": round(statistics.median(latencies), 1) if latencies else None,
            "ai_p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1) if latencies else None,
            "n": len(latencies),
            "human_escalation_first_response": {
                "value_hours": None,
                "note": "not available: the demo escalation queue records no human first-response time; "
                        "the business context states ~9 h today"},
        },
        "reopen": ({"simulated_reopen_rate_pct": reopen.get("simulated_reopen_rate_pct"),
                    "n": reopen.get("n"), "label": "SIMULATED (week12/reopen_eval.json)"} if reopen
                   else {"simulated_reopen_rate_pct": None, "n": 0,
                         "label": "not run yet: python main.py capstone reopen"}),
        "by_status": ticket_store.counts()["by_status"],
        "recent": [{k: r.get(k) for k in ("trace_id", "ts", "ticket_id", "status", "refused", "latency_ms", "cost_usd",
                                          "input", "output", "policy_verdict")} for r in rows[:12]],
    }


def tickets_list(query):
    status = (query.get("status") or [None])[0]
    q = (query.get("q") or [None])[0]
    rows = ticket_store.list_tickets(status=status or None, q=q or None, include_pii=False)
    slim = []
    for r in rows:
        ai = r.get("ai") or {}
        slim.append({k: r.get(k) for k in ("ticket_id", "customer_id", "subject", "created_at", "priority", "status",
                                           "assigned_agent", "order_id", "tags", "source", "channel")}
                    | {"customer_name": (r.get("customer") or {}).get("name"), "tier": (r.get("customer") or {}).get("tier"),
                       "ai": {k: ai.get(k) for k in ("outcome", "decision", "trace_id", "intent", "ran_at")} if ai else None,
                       "description_preview": (r.get("description") or "")[:140]})
    return {"tickets": slim, "count": len(slim), "integration": INTEGRATION_LABELS["inbox"]}


def ticket_detail(ticket_id):
    # The agent console is the authorised UI: the customer's email is shown here and nowhere else.
    t = ticket_store.get_ticket(ticket_id, include_pii=True)
    if not t:
        return None
    t["escalations"] = escalation_queue.list_escalations(ticket_id=t["ticket_id"])
    t["orders"] = business_tools.lookup_order(customer_id=t["customer_id"]).get("orders", [])
    t["entitlement"] = business_tools.check_entitlement(t["customer_id"], t.get("order_id"))
    t["pii_scope"] = "agent_console: email shown to the authorised agent; never written to traces or logs"
    return t


def trace(trace_id):
    found = obs.get(trace_id)
    if found is None:
        return None
    lines = []
    if ACCESS_LOG.exists():
        for line in ACCESS_LOG.read_text(encoding="utf-8").splitlines()[-5000:]:
            if trace_id in line:
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    found["mcp_server_log"] = {"file": ACCESS_LOG.relative_to(ROOT).as_posix(), "lines": lines,
                               "note": "lines the MCP SERVER wrote for this trace id: the id crossed the client/server boundary"}
    found["capstone"] = (found.get("trace") or {}).get("capstone")
    return found


def traces_list(query):
    limit = min(int((query.get("limit") or ["50"])[0]), obs.MAX_LIMIT)
    rows = deflection_rows(limit)
    return {"rows": rows, "count": len(rows)}


def article(slug):
    import db
    slug = (slug or "").strip().lower()
    for doc in db.load_documents():
        m = doc["metadata"]
        s = (m.get("slug") or re.sub(r"[^a-z0-9]+", "-", (m.get("title") or m.get("article_id") or "").lower()).strip("-"))
        if s == slug or str(m.get("article_id", "")).lower() == slug:
            return {"found": True, "slug": s, "article_id": m.get("article_id"), "title": m.get("title"),
                    "last_updated": str(m.get("last_updated")), "status": m.get("status") or "current",
                    "policy_version": m.get("policy_version"), "superseded_by": m.get("superseded_by"),
                    "supersedes": m.get("supersedes"), "product_area": m.get("product_area"),
                    "doc_type": m.get("doc_type") or "article", "source_file": m.get("source_file"),
                    "markdown": doc["content"]}
    return None


def mcp_status(discover=False):
    status = mcp_session.status()
    if discover:
        host = mcp_session.get_host(refresh_tools=True)
        status = mcp_session.status()
        status["discovered_now"] = sorted(host.tools) if host else []
        status["schemas"] = host.tool_schemas() if host else []
    status["how_to_connect"] = _text_file(ROOT / "docs" / "mcp_helpcentre.md")
    return status


def evaluation():
    """Every number the Evaluation screen shows, read from the files that are the evidence."""
    week6_agreement = _json_file(ROOT / "week6" / "agreement.json") or {}
    integrated = _json_file(WEEK12_DIR / "integrated_eval.json")
    trajectory = _json_file(WEEK12_DIR / "trajectory_eval.json")
    taxonomy = _json_file(WEEK12_DIR / "error_taxonomy.json")
    reopen = _json_file(WEEK12_DIR / "reopen_eval.json")
    cost = _json_file(WEEK12_DIR / "cost_and_scaling.json")
    closed_loop = _json_file(WEEK12_DIR / "closed_loop.json")
    return {
        "generated_at": now_iso(),
        "judge_validation": {"source": "week6/agreement.json (25 blind hand labels, Week 6)",
                             "agreement_after_pct": week6_agreement.get("agreement_after"),
                             "held_out": (week6_agreement.get("agreement_after_detail") or {}).get("held_out"),
                             "kappa": (week6_agreement.get("agreement_after_detail") or {}).get("cohen_kappa"),
                             "target_pct": 85},
        "integrated": integrated, "trajectory": trajectory, "taxonomy": taxonomy, "reopen": reopen, "cost": cost,
        "closed_loop": closed_loop,
        "docs": {"weaknesses_md": _text_file(ROOT / "docs" / "wednesday_weaknesses.md"),
                 "integrated_report_md": _text_file(REPORTS_DIR / "integrated_eval_report.md"),
                 "taxonomy_md": _text_file(REPORTS_DIR / "error_taxonomy.md"),
                 "trajectory_md": _text_file(REPORTS_DIR / "trajectory_eval.md"),
                 "cost_md": _text_file(REPORTS_DIR / "cost_and_scaling.md")},
        "missing": [name for name, value in (("integrated_eval.json", integrated), ("trajectory_eval.json", trajectory),
                                             ("error_taxonomy.json", taxonomy), ("reopen_eval.json", reopen),
                                             ("cost_and_scaling.json", cost)) if value is None],
    }


def sample():
    return _json_file(WEEK12_DIR / "sample.json") or {"error": "week12/sample.json not drawn yet"}


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def _policy_filter(payload):
    """The policy-version filter can be switched off only in demo mode (to show the v1.0 failure)."""
    if payload.get("policy_filter", True) is False:
        if DEPLOYMENT_MODE != "demo":
            raise ValueError("policy_filter cannot be disabled outside demo mode")
        return False
    return True


def _planner(payload):
    planner = payload.get("planner") or "model"
    if planner not in PLANNERS:
        raise ValueError(f"planner must be one of {PLANNERS}")
    return planner


def analyse_ticket(ticket_id, payload):
    from capstone.deflection_agent import handle_ticket
    t = ticket_store.get_ticket(ticket_id)
    if not t:
        raise KeyError(ticket_id)
    planner = _planner(payload)
    policy_filter = _policy_filter(payload)
    with obs.request("deflection", source="ui-inbox", input_text=mask_text(t["description"]),
                     route=f"/api/capstone/tickets/{t['ticket_id']}/analyse"):
        result = handle_ticket({"ticket_id": t["ticket_id"], "customer_id": t["customer_id"],
                                "message": t["description"], "order_id": t.get("order_id"), "subject": t.get("subject")},
                               source="ui-inbox", planner=planner, policy_filter=policy_filter)
    summary = {k: result.get(k) for k in ("trace_id", "outcome", "decision", "intent", "answer", "policy_version",
                                          "policy_label", "latency_ms", "escalation", "injection_detected", "planner",
                                          "policy_filter", "prompt_version")}
    summary["citations"] = result["citations"]["cited"]
    summary["consulted"] = result["citations"]["consulted"]
    summary["tools"] = [{k: s.get(k) for k in ("step", "name", "status", "duration_ms", "kind", "server", "forced_by", "summary")}
                        for s in result["timeline"]]
    summary["cost_usd"] = result["meter"]["cost_usd"]
    summary["tokens"] = result["meter"]["tokens"]
    summary["assertions"] = {"passed": result["evaluation"]["passed"], "failed": result["evaluation"]["failed_names"]}
    summary["ran_at"] = now_iso()
    ticket_store.record_ai_result(t["ticket_id"], summary)
    return {"result": result, "ticket": ticket_store.get_ticket(t["ticket_id"], include_pii=True)}


def ticket_action(ticket_id, payload):
    action = payload.get("action")
    by = (payload.get("agent") or "agent").strip()[:40]
    t = ticket_store.get_ticket(ticket_id)
    if not t:
        raise KeyError(ticket_id)
    if action == "approve":
        reply = (payload.get("reply") or (t.get("ai") or {}).get("answer") or "").strip()
        if not reply:
            raise ValueError("nothing to approve: run the AI analysis first or provide a reply")
        edited = reply != ((t.get("ai") or {}).get("answer") or "")
        return ticket_store.update_ticket(ticket_id, by=by, status="answered", approved_reply=reply,
                                         action="edit_and_approve" if edited else "approve",
                                         note=payload.get("note"))
    if action == "escalate":
        reason = (payload.get("reason") or "escalated by the agent").strip()
        record = business_tools.escalate_ticket(t["ticket_id"], reason, priority=payload.get("priority") or "normal",
                                                notes=f"manual escalation by {by}", trace_id=(t.get("ai") or {}).get("trace_id"))
        ticket_store.update_ticket(ticket_id, by=by, status="escalated", action="escalate",
                                   note=f"{reason} -> {record.get('reference')} ({record.get('queue')})")
        return {**ticket_store.get_ticket(ticket_id, include_pii=True), "escalation": record}
    if action == "resolve":
        return ticket_store.update_ticket(ticket_id, by=by, status="resolved", action="resolve", note=payload.get("note"))
    if action == "assign":
        return ticket_store.update_ticket(ticket_id, by=by, assigned_agent=payload.get("assigned_agent") or by,
                                         action="assign")
    if action == "reopen":
        return ticket_store.update_ticket(ticket_id, by=by, status="new", action="reopen", note=payload.get("note"))
    raise ValueError("action must be approve, escalate, resolve, assign or reopen")


def chat(payload):
    from capstone.deflection_agent import handle_ticket
    message = (payload.get("message") or "").strip()
    if not message or len(message) > 4000:
        raise ValueError("message must be 1-4000 characters")
    planner = _planner(payload)
    conv = conversation_store.get(payload.get("conversation_id")) if payload.get("conversation_id") else None
    if conv is None:
        conv = conversation_store.create(customer_id=(payload.get("customer_id") or "").strip().upper() or None,
                                         ticket_id=(payload.get("ticket_id") or "").strip().upper() or None)
    history = conversation_store.recent_turns(conv)
    conversation_store.append_turn(conv["conversation_id"], "customer", message)
    ticket_id = conv.get("ticket_id") or f"CD-CHAT-{conv['conversation_id'][-6:].upper()}"
    with obs.request("deflection", source="ui-chat", input_text=mask_text(message), route="/api/capstone/chat"):
        result = handle_ticket({"ticket_id": ticket_id, "customer_id": conv.get("customer_id"), "message": message},
                               source="ui-chat", planner=planner, policy_filter=_policy_filter(payload),
                               history=history, session_id=conv["conversation_id"])
    conv = conversation_store.append_turn(conv["conversation_id"], "assistant", result["answer"] or "",
                                          trace_id=result["trace_id"], outcome=result["outcome"],
                                          decision=result["decision"], policy_version=result["policy_version"],
                                          citations=result["citations"]["cited"], escalation=result["escalation"])
    return {"conversation": conv, "result": result}


# ---------------------------------------------------------------------------
# dispatcher
# ---------------------------------------------------------------------------

def handle(handler, method, parsed, payload, exclusive, busy_cls):
    """Route one /api/capstone/* request. Returns True when handled."""
    path = parsed.path
    query = urllib.parse.parse_qs(parsed.query)
    send = handler._send_json

    def ok(data, status=200):
        send({"status": "ok", **data} if isinstance(data, dict) and "status" not in data else data, status=status)

    if method == "GET":
        if path == "/api/capstone/health":
            return ok(health()) or True
        if path == "/api/capstone/dashboard":
            return ok(dashboard()) or True
        if path == "/api/capstone/tickets":
            return ok(tickets_list(query)) or True
        m = re.fullmatch(r"/api/capstone/tickets/([A-Za-z0-9\-]+)", path)
        if m:
            t = ticket_detail(m.group(1))
            if t is None:
                send({"status": "error", "error": f"no ticket {m.group(1)}"}, status=404)
            else:
                ok({"ticket": t})
            return True
        if path == "/api/capstone/escalations":
            return ok({"escalations": escalation_queue.list_escalations((query.get("status") or [None])[0]),
                       "integration": INTEGRATION_LABELS["escalation"]}) or True
        if path == "/api/capstone/traces":
            return ok(traces_list(query)) or True
        if path == "/api/capstone/trace":
            trace_id = (query.get("trace_id") or [""])[0].strip()
            if not trace_id:
                raise ValueError("Missing trace_id")
            found = trace(trace_id)
            if found is None:
                send({"status": "error", "error": f"No trace {trace_id}"}, status=404)
            else:
                ok(found)
            return True
        if path == "/api/capstone/evaluation":
            return ok(evaluation()) or True
        if path == "/api/capstone/sample":
            return ok(sample()) or True
        if path == "/api/capstone/mcp":
            return ok(mcp_status(discover=(query.get("discover") or ["0"])[0] in ("1", "true"))) or True
        if path == "/api/capstone/conversations":
            return ok({"conversations": conversation_store.list_conversations()}) or True
        m = re.fullmatch(r"/api/capstone/conversations/([A-Za-z0-9\-]+)", path)
        if m:
            conv = conversation_store.get(m.group(1))
            if conv is None:
                send({"status": "error", "error": "no such conversation"}, status=404)
            else:
                ok({"conversation": conv})
            return True
        m = re.fullmatch(r"/api/capstone/articles/([A-Za-z0-9\-_.]+)", path)
        if m:
            found = article(m.group(1))
            if found is None:
                send({"status": "error", "error": f"no article {m.group(1)}"}, status=404)
            else:
                ok(found)
            return True
        return False

    # POST
    if path == "/api/capstone/chat":
        with exclusive("capstone chat"):
            ok(chat(payload))
        return True
    m = re.fullmatch(r"/api/capstone/tickets/([A-Za-z0-9\-]+)/analyse", path)
    if m:
        try:
            with exclusive(f"capstone analysis of {m.group(1)}"):
                ok(analyse_ticket(m.group(1), payload))
        except KeyError:
            send({"status": "error", "error": f"no ticket {m.group(1)}"}, status=404)
        return True
    m = re.fullmatch(r"/api/capstone/tickets/([A-Za-z0-9\-]+)/action", path)
    if m:
        try:
            ok({"ticket": ticket_action(m.group(1), payload)})
        except KeyError:
            send({"status": "error", "error": f"no ticket {m.group(1)}"}, status=404)
        return True
    m = re.fullmatch(r"/api/capstone/escalations/([A-Za-z0-9\-]+)", path)
    if m:
        status = payload.get("status")
        if status not in ("open", "in_progress", "resolved"):
            raise ValueError("status must be open, in_progress or resolved")
        escalation_queue.update(m.group(1), status=status, notes=payload.get("notes"), by=payload.get("agent") or "agent")
        ok({"escalation": escalation_queue.get(m.group(1))})
        return True
    return False
