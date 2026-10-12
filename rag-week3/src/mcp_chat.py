"""MCP Customer Chatbot Handler.

Nothing here names a tool. For every message the host:

  1. discovers tools from whichever MCP servers the config names (tools/list),
  2. reads each tool's inputSchema and fills what it can from the question:
       - an id-like parameter (ticket_id, article_id, ...) gets each id-shaped
         token in the question (TCK-4471, HC-003, MY-GUIDE)
       - a free-text string parameter (query, ...) gets the question itself
       - an optional count (top_k, limit, ...) gets top_k
     A tool with a required parameter it cannot fill is skipped,
  3. calls every tool it could fill, and keeps only results that carry data
     (an error or an empty result means "this tool has nothing for that"),
  4. shows that data, or "no relevant data" when no tool returned any.

Add a server to the config and its tools take part on the next message.
"""

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcp_agent import Host  # noqa: E402

SERVER_DESCRIPTIONS = {
    "clouddesk-kb": {
        "name": "CloudDesk Knowledge Base Server",
        "description": "Internal RAG corpus covering help-centre articles & uploaded customer documents in Postgres.",
        "badge_color": "ok",
        "icon": "⚡"
    },
    "ticket-history": {
        "name": "Support Ops Ticket Store",
        "description": "Third-party customer support ticket repository & escalation audit ledger.",
        "badge_color": "violet",
        "icon": "🎫"
    },
    "gateway": {
        "name": "CloudDesk MCP Gateway",
        "description": "Unified front door proxying to all backend MCP servers with token scoping and audit logs.",
        "badge_color": "info",
        "icon": "🛡️"
    }
}

# A hyphenated token that has a digit or is written in capitals: TCK-4471,
# HC-003, MY-GUIDE. Not "sign-in" or "e-mail".
ID_TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9_]+)+\b")
COUNT_PARAMS = {"top_k", "k", "limit", "max_results", "n"}
ID_KEYS = ("article_id", "ticket_id")
MAX_ITEMS_PER_TOOL = 3
MAX_TEXT_CHARS = 900


def _candidate_ids(query):
    ids = []
    for token in ID_TOKEN.findall(query):
        if any(ch.isdigit() for ch in token) or token == token.upper():
            for variant in (token.upper(), token):
                if variant not in ids:
                    ids.append(variant)
    return ids


def _is_id_param(name):
    return name == "id" or name.endswith("_id") or name.endswith("Id")


def _plan_calls(tools, query, top_k):
    """(key, server, arguments) for every call the schemas allow, plus why others were skipped."""
    ids = _candidate_ids(query)
    calls, skipped = [], []
    for key, (server, tool) in sorted(tools.items()):
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []

        base, id_params, missing = {}, [], []
        for name in required:
            ptype = (props.get(name) or {}).get("type", "string")
            if _is_id_param(name):
                id_params.append(name)
            elif ptype == "string" and query not in base.values():
                base[name] = query      # one free-text parameter per tool
            else:
                missing.append(name)
        for name, spec in props.items():
            if name not in required and name in COUNT_PARAMS and spec.get("type") == "integer":
                base[name] = top_k

        if missing:
            skipped.append({"tool": key, "server": server,
                            "reason": f"needs {', '.join(missing)}"})
        elif len(id_params) > 1:
            skipped.append({"tool": key, "server": server,
                            "reason": f"needs {', '.join(id_params)}"})
        elif id_params:
            if not ids:
                skipped.append({"tool": key, "server": server,
                                "reason": f"needs {id_params[0]}; none in the question"})
            for value in ids:
                calls.append((key, server, {**base, id_params[0]: value}))
        else:
            calls.append((key, server, base))
    return calls, skipped


def _payload_items(result):
    """Decoded items a tool returned: structured content first, then each text block."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and set(structured) == {"result"}:
        structured = structured["result"]
    if structured not in (None, {}, []):
        return structured if isinstance(structured, list) else [structured]
    items = []
    for content in result.get("content", []):
        text = content.get("text", "")
        try:
            items.append(json.loads(text))
        except (TypeError, ValueError):
            items.append(text)
    flat = []
    for item in items:
        flat.extend(item if isinstance(item, list) else [item])
    return [item for item in flat if item not in (None, "", [], {})
            and not (isinstance(item, str) and not item.strip())]


def _evidence(key, server, arguments, items):
    evidence = []
    for item in items:
        entry = {"tool": key, "server": server}
        if isinstance(item, dict):
            for id_key in ID_KEYS:
                value = item.get(id_key) or arguments.get(id_key)
                if value:
                    entry[id_key] = value
            if item.get("section"):
                entry["section"] = item["section"]
            entry["type"] = "kb_passage" if "text" in item else "ticket_data" if "ticket_id" in entry else "result"
            entry["data"] = item
        else:
            for id_key in ID_KEYS:
                if arguments.get(id_key):
                    entry[id_key] = arguments[id_key]
            entry["type"] = "full_text"
            entry["data"] = str(item)
        evidence.append(entry)
    return evidence


def _label(key):
    return key.replace("_", " ").capitalize()


def _best_section(text, query):
    """The markdown section of a long record that shares the most words with the question."""
    from grounding import content_words
    wanted = set(content_words(query))
    sections = re.split(r"\n(?=#{1,6} )", text)
    best = max(sections, key=lambda s: len(wanted & set(content_words(s))))
    if not wanted & set(content_words(best)):
        best = sections[0]
    best = best.strip()
    return best if len(best) <= MAX_TEXT_CHARS else best[:MAX_TEXT_CHARS] + "…"


def _render_item(item, query):
    data = item["data"]
    if isinstance(data, str):
        text = data.strip()
        if len(text) <= MAX_TEXT_CHARS:
            return text
        return _best_section(text, query) + "\n\n*(most relevant section of a longer record)*"
    if "text" in data:
        tag = " · ".join(str(v) for v in (data.get("article_id") or data.get("ticket_id"),
                                          data.get("section")) if v)
        body = str(data["text"]).strip().replace("\n", "\n> ")
        return f"**[{tag}]**\n> {body}" if tag else f"> {body}"
    return "\n".join(f"- **{_label(k)}**: `{v}`" for k, v in data.items())


def _render_answer(hits, query):
    blocks = []
    for hit in hits:
        items = hit["evidence"][:MAX_ITEMS_PER_TOOL]
        args = ", ".join(f"{k}={v}" for k, v in hit["arguments"].items() if k not in COUNT_PARAMS)
        heading = f"#### 🔧 `{hit['tool']}`({args}) · `{hit['server']}`"
        if all(isinstance(e["data"], dict) and "text" not in e["data"] for e in items) and len(items) > 1:
            rows = [f"{i}. " + " · ".join(f"**{_label(k)}**: {v}" for k, v in e["data"].items())
                    for i, e in enumerate(items, 1)]
            body = "\n".join(rows)
        else:
            body = "\n\n".join(_render_item(e, query) for e in items)
        more = len(hit["evidence"]) - len(items)
        blocks.append(heading + "\n\n" + body + (f"\n\n*…and {more} more*" if more > 0 else ""))
    return "\n\n".join(blocks)


def _select_hits(hits, ids):
    """Which hits answer the question.

    A lookup by an id named in the question is the precise answer. If a search
    also returned passages from that same record, those passages are the
    relevant part of it and are shown instead of the whole record.
    """
    by_id = [h for h in hits if set(h["arguments"].values()) & set(ids)]
    if not by_id:
        if not ids:
            return hits
        # The question names a record no lookup found (TCK-9999). A search
        # hit counts only if it mentions that id; matching the word "ticket"
        # is not data about TCK-9999.
        wanted = [i.lower() for i in ids]
        kept = []
        for hit in hits:
            same = [e for e in hit["evidence"]
                    if any(w in json.dumps(e["data"]).lower() for w in wanted)]
            if same:
                kept.append({**hit, "evidence": same})
        return kept
    hit_ids = {v for h in by_id for v in h["arguments"].values()}
    focused = []
    for hit in hits:
        if hit in by_id:
            continue
        same = [e for e in hit["evidence"] if {e.get(k) for k in ID_KEYS} & hit_ids]
        if same:
            focused.append({**hit, "evidence": same})
    covered = {e.get(k) for f in focused for e in f["evidence"] for k in ID_KEYS}
    return focused + [h for h in by_id if not set(h["arguments"].values()) & covered]


def handle_mcp_chat(query: str, which_config: str = "server_two", top_k: int = 3) -> dict:
    """Call every discovered MCP tool the question can fill; show the data, or no data."""
    query = (query or "").strip()
    if not query:
        raise ValueError("Query cannot be empty")
    top_k = max(1, min(int(top_k or 3), 5))

    config_file = "week9/mcp_config_gateway.json" if which_config == "gateway" else "week9/mcp_config.json"
    wire = []
    host = Host(str(ROOT / config_file), wire=wire)

    try:
        started = time.perf_counter()
        inventory = host.connect()
        planned, skipped = _plan_calls(host.tools, query, top_k)

        steps = [{**s, "arguments": None, "status": "skipped", "items": 0} for s in skipped]
        hits = []
        for key, server, arguments in planned:
            result, server = host.call(key, arguments)
            items = [] if result.get("isError") else _payload_items(result)
            status = "error" if result.get("isError") else "data" if items else "empty"
            steps.append({"tool": key, "server": server, "arguments": arguments,
                          "status": status, "items": len(items)})
            if items:
                hits.append({"tool": key, "server": server, "arguments": arguments,
                             "evidence": _evidence(key, server, arguments, items)})
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        shown = _select_hits(hits, _candidate_ids(query))
        evidence = [e for h in shown for e in h["evidence"][:MAX_ITEMS_PER_TOOL]]
        called = [s for s in steps if s["status"] != "skipped"]
        if shown:
            answer = (_render_answer(shown, query) + "\n\n---\n"
                      f"*Checked {len(called)} MCP tool call(s); {len(hits)} returned data.*")
        else:
            tried = ", ".join(sorted({f"`{s['tool']}`" for s in called})) or "no tool"
            answer = (f"**No relevant data found** for *'{query}'*.\n\n"
                      f"Checked {tried} on the connected MCP servers; none returned data for "
                      f"this question. Upload the relevant document or rephrase the question.")

        primary = shown[0] if shown else None
        server_name = primary["server"] if primary else None
        server_meta = SERVER_DESCRIPTIONS.get(server_name, {
            "name": server_name or "none",
            "description": (f"MCP server '{server_name}'" if server_name
                            else "No MCP tool returned data for this question."),
            "badge_color": "info",
            "icon": "⚡"
        })
        call_wire = [m for m in wire if m.get("message", {}).get("method") == "tools/call"]

        return {
            "status": "ok",
            "query": query,
            "answer": answer,
            "found": bool(shown),
            "mcp": {
                "server": server_name,
                "server_info": server_meta,
                "tool": primary["tool"] if primary else None,
                "arguments": primary["arguments"] if primary else None,
                "is_error": False,
                "transport": "stdio (child process)",
                "protocol_version": "2025-06-18",
                "latency_ms": latency_ms,
                "wire": call_wire[-8:],
                "all_servers": list(inventory.keys()),
                "steps": steps,
            },
            "evidence": evidence,
            "discovered_tools": sorted(host.tools.keys()),
        }
    finally:
        host.close()
