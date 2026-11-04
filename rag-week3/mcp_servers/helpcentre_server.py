"""Week 12: the help-centre search MCP server.

Exposes the Weeks 3-4 retrieval pipeline (dense + BM25 + RRF + cross-encoder
over the Postgres corpus) as an MCP tool any client can call:

    tools      search_help_centre(query, top_k, policy_version, product_area)
               get_help_centre_article(slug_or_id)
    resources  helpcentre://articles/{slug}

Every passage comes back with the article slug, article id, title, last
updated date, section, policy version (for the refund policy articles),
status (current / superseded) and a clickable URL, so the caller can cite
exactly what was retrieved and nothing else.

Trace propagation: the client puts its trace_id in params._meta. The tool
reads it from the MCP request context, writes it to the access log
(week12/logs/mcp_helpcentre_access.log, PII-free) and echoes it in the
result, so the same id is visible on both sides of the boundary.

Transports:
    venv/bin/python mcp_servers/helpcentre_server.py              # stdio
    venv/bin/python mcp_servers/helpcentre_server.py --http 8765  # streamable HTTP at /mcp

No model runs here. The server returns passages; the host's model writes.
"""

import contextlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mcp.server.mcpserver import Context, MCPServer  # noqa: E402

SERVER_NAME = "clouddesk-helpcentre"
SERVER_VERSION = "1.0.0"
ACCESS_LOG = os.path.join(ROOT, "week12", "logs", "mcp_helpcentre_access.log")
HELP_CENTRE_PATH = "/app/help-centre/"
MAX_TOP_K = 8
MAX_TEXT = 900
POLICY_VERSIONS = ("current-30-day", "legacy-14-day")

server = MCPServer(SERVER_NAME, version=SERVER_VERSION, instructions=(
    "CloudDesk help-centre search. Call search_help_centre with the customer's question; pass "
    "policy_version when the account's refund policy version is known so only that policy is "
    "returned. Cite only the slugs this server returns."))


def _quiet():
    """stdout is the JSON-RPC channel on stdio: anything the RAG code prints goes to stderr."""
    return contextlib.redirect_stdout(sys.stderr)


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _trace_id(ctx):
    """The caller's trace id from params._meta, if it sent one."""
    try:
        meta = ctx.request_context.meta if ctx is not None else None
        if meta is None:
            return None
        if isinstance(meta, dict):
            return meta.get("trace_id")
        value = getattr(meta, "trace_id", None)
        if value is None and hasattr(meta, "model_extra"):
            value = (meta.model_extra or {}).get("trace_id")
        if value is None and hasattr(meta, "model_dump"):
            value = meta.model_dump().get("trace_id")
        return value
    except Exception:
        return None


def _log(tool, arguments, trace_id, ms, n):
    """One PII-free line per call: tool, argument shape, trace id, latency, hits."""
    try:
        os.makedirs(os.path.dirname(ACCESS_LOG), exist_ok=True)
        safe_args = {k: (v if k != "query" else f"<{len(str(v))} chars>") for k, v in (arguments or {}).items()}
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "server": SERVER_NAME,
                "tool": tool, "arguments": safe_args, "trace_id": trace_id, "latency_ms": ms, "results": n,
                "pid": os.getpid()}
        with open(ACCESS_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")
    except Exception as error:           # the log must never fail the call
        print(f"[{SERVER_NAME}] log failed: {error}", file=sys.stderr)


def _documents():
    with _quiet():
        import db
        return db.load_documents()


def _doc_index():
    docs = {}
    for doc in _documents():
        meta = doc["metadata"]
        slug = meta.get("slug") or _slugify(meta.get("title") or meta.get("article_id"))
        docs[slug] = (doc, meta)
        docs[str(meta.get("article_id", "")).upper()] = (doc, meta)
    return docs


def _passage(chunk):
    meta_title = chunk.get("title")
    slug = chunk.get("slug") or _slugify(meta_title or chunk.get("article_id"))
    return {
        "article_id": chunk.get("article_id"), "slug": slug, "title": meta_title,
        "section": chunk.get("section"), "product_area": chunk.get("product_area"),
        "last_updated": str(chunk.get("last_updated")) if chunk.get("last_updated") else None,
        "policy_version": chunk.get("policy_version"), "status": chunk.get("status") or "current",
        "superseded_by": chunk.get("superseded_by"), "doc_type": chunk.get("doc_type") or "article",
        "chunk_id": chunk.get("chunk_id"), "url": f"{HELP_CENTRE_PATH}{slug}",
        "scores": {k: chunk.get(k) for k in ("distance", "bm25_score", "rrf_score", "rerank_score",
                                              "dense_rank", "bm25_rank") if chunk.get(k) is not None},
        "text": (chunk.get("content") or "")[:MAX_TEXT],
    }


@server.tool()
def search_help_centre(query: str, top_k: int = 5, policy_version: str | None = None,
                       product_area: str | None = None, ctx: Context | None = None) -> dict:
    """Search the CloudDesk help centre (articles, policies, macros, changelog, shipping SLA)
    with hybrid retrieval: dense + BM25 fused by RRF and reranked by a cross-encoder.

    Returns the best passages with slug, article_id, title, last_updated, section, policy_version,
    status (current or superseded) and a clickable url, plus the trace_id the caller sent.
    policy_version ("current-30-day" or "legacy-14-day") keeps only that refund policy's passages
    and drops the other version; pass it once the customer's entitlement has been verified.
    """
    started = time.perf_counter()
    trace_id = _trace_id(ctx)
    query = (query or "").strip()
    if not query:
        return {"error": "empty_query", "results": [], "trace_id": trace_id}
    top_k = max(1, min(int(top_k or 5), MAX_TOP_K))
    if policy_version and policy_version not in POLICY_VERSIONS:
        return {"error": "bad_policy_version", "allowed": list(POLICY_VERSIONS), "results": [], "trace_id": trace_id}
    with _quiet():
        from hybrid_retriever import retrieve_hybrid
        from retriever import build_where
        where = build_where(product_area=product_area) if product_area else None
        # Retrieve a few more than asked so a version filter still leaves top_k.
        chunks = retrieve_hybrid(query, top_k=top_k + 3, where=where, rerank=True)
    dropped = []
    results = []
    for chunk in chunks:
        p = _passage(chunk)
        if policy_version and p["policy_version"] and p["policy_version"] != policy_version:
            dropped.append({"slug": p["slug"], "policy_version": p["policy_version"],
                            "reason": f"other policy version (account is on {policy_version})"})
            continue
        results.append(p)
    results = results[:top_k]
    for rank, p in enumerate(results, 1):
        p["rank"] = rank
    ms = round((time.perf_counter() - started) * 1000, 1)
    _log("search_help_centre", {"query": query, "top_k": top_k, "policy_version": policy_version,
                                "product_area": product_area}, trace_id, ms, len(results))
    return {"query": query, "results": results, "dropped": dropped, "policy_version_filter": policy_version,
            "retrieval": "hybrid (dense + BM25, RRF k=60) + cross-encoder rerank; markdown index",
            "latency_ms": ms, "trace_id": trace_id, "server": SERVER_NAME}


@server.tool()
def get_help_centre_article(slug_or_id: str, ctx: Context | None = None) -> dict:
    """Fetch one help-centre article by slug (refund-returns-current-30-day) or id (HC-008).
    Returns the markdown and its metadata (last_updated, status, policy_version, url)."""
    started = time.perf_counter()
    trace_id = _trace_id(ctx)
    key = (slug_or_id or "").strip()
    found = _doc_index().get(key) or _doc_index().get(key.upper()) or _doc_index().get(_slugify(key))
    ms = round((time.perf_counter() - started) * 1000, 1)
    if not found:
        _log("get_help_centre_article", {"slug_or_id": key}, trace_id, ms, 0)
        return {"found": False, "error": "article_not_found", "slug_or_id": key, "trace_id": trace_id,
                "known": sorted(k for k in _doc_index() if not k.startswith("HC-") and not k.startswith("MACRO"))}
    doc, meta = found
    _log("get_help_centre_article", {"slug_or_id": key}, trace_id, ms, 1)
    slug = meta.get("slug") or _slugify(meta.get("title") or meta.get("article_id"))
    return {"found": True, "article_id": meta.get("article_id"), "slug": slug, "title": meta.get("title"),
            "last_updated": str(meta.get("last_updated")), "status": meta.get("status") or "current",
            "policy_version": meta.get("policy_version"), "product_area": meta.get("product_area"),
            "url": f"{HELP_CENTRE_PATH}{slug}", "markdown": doc["content"], "trace_id": trace_id}


@server.resource("helpcentre://articles/{slug}", mime_type="text/markdown")
def article_resource(slug: str) -> str:
    found = _doc_index().get(slug)
    return found[0]["content"] if found else f"No article {slug}."


if __name__ == "__main__":
    if "--http" in sys.argv:
        idx = sys.argv.index("--http")
        port = int(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 8765
        print(f"[{SERVER_NAME}] streamable HTTP on http://127.0.0.1:{port}/mcp", file=sys.stderr)
        server.run("streamable-http", host="127.0.0.1", port=port, json_response=True)
    else:
        server.run("stdio")
