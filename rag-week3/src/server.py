"""Web UI server for CloudDesk Knowledge Studio (RAG).

Serves the single-page UI from ``src/static/index.html`` and a small JSON API:

  GET  /                      UI
  GET  /api/documents         indexed documents (metadata + size)
  GET  /api/status            per-strategy index status
  POST /api/ask               grounded answer + retrieval stages
  POST /api/inspect           retrieval stages + grounded answer
  POST /api/upload            upload ANY file (text or base64) and index it
  GET  /api/week5             error-analysis artefacts (taxonomy, notes, sample)
  POST /api/trace             one trace by trace_id
  POST /api/week5/rerun       re-ask the 20 sampled questions on the current build
  GET  /api/week6             judge-validation artefacts (cases, runs, agreement, protocol)
  POST /api/week6/run         the one eval command over the frozen replies
  GET  /api/week7             agent vs workflow race: the 4 numbers, per-ticket transcripts, budgets
  POST /api/week7/workflow    run the fixed workflow on one race ticket (live, 1.5B model)
  POST /api/week7/agent       run the agent loop on one race ticket under four budgets (live)
  GET  /api/week8             trajectory eval: sequences, numbers, gap, mitigation, regression
  POST /api/week8/rescore     rescore the stored trajectory runs in memory (no model, no writes)
  GET  /api/week9             MCP: discovery, diffs, annotated wire, transcripts, risk, gateway
  POST /api/week9/discover    live initialize + tools/list (no model) for one config
  POST /api/week9/wire        live initialize -> tools/list -> tools/call against server two (no model)
  POST /api/week9/chat        customer chat query via MCP servers with server attribution
  GET  /api/reports           the markdown deliverables (DEMO_GUIDE, READMEs, results) for the UI
  GET  /api/health            store reachable, index counts, build, what is running
  GET  /api/runs              which long-running action holds the model lock right now
  GET  /api/golden            the 12 golden questions
  POST /api/golden/evaluate   hit-rate@k + p50 latency, baseline vs one change
  POST /api/reindex           rebuild every index
  POST /api/documents/delete  remove a document and re-index

Concurrency: the server is threaded so the evidence views stay readable
while a model-backed action runs, but every action that touches a model or
the index takes RUN_LOCK without waiting. A second expensive request while
one is in flight gets 409 and the name of the run in progress, instead of
quietly queueing a duplicate.
"""

import base64
import http.server
import json
import os
import re
import sys
import threading
import traceback
import shutil
import tempfile
import time
import urllib.parse
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure src is in python path
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from generator import generate_answer, RELEVANCE_THRESHOLD
import golden_eval
from retriever import retrieve_chunks, build_where
from bm25_retriever import retrieve_bm25, clear_bm25_cache
from hybrid_retriever import reciprocal_rank_fusion
from reranker import rerank_chunks
from query_transform import rewrite_query, generate_hypothetical_document
import db
from loader import (
    load_articles,
    extract_text_from_file,
    split_frontmatter,
    derive_title,
    ExtractionError,
    META_SUFFIX,
    TEXT_FRONTMATTER_EXTS,
)
from vector_store import (
    STRATEGIES,
    DEFAULT_STRATEGY,
    build_all_indexes,
    index_counts,
)

STATIC_DIR = Path(SRC_DIR) / "static"
INDEX_HTML = STATIC_DIR / "index.html"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def safe_filename(name: str) -> str:
    """Strip directories and characters that are unsafe on disk."""
    name = Path(name.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.\-() ]+", "_", name)
    name = name.strip(" .")
    if name.endswith(META_SUFFIX):
        name = name[: -len(META_SUFFIX)]
    return name


def rebuild_indexes():
    results = build_all_indexes()
    clear_bm25_cache()
    return {r["strategy"]: r["chunks"] for r in results}


def index_status():
    counts = index_counts()
    return {
        strategy: {
            "count": counts.get(strategy, 0),
            "status": "ready" if counts.get(strategy) else "not_built",
        }
        for strategy in STRATEGIES
    }


def document_list():
    docs = []
    for d in load_articles():
        m = d["metadata"]
        docs.append({
            "source_file": m.get("source_file"),
            "article_id": m.get("article_id"),
            "title": m.get("title"),
            "product_area": m.get("product_area"),
            "last_updated": m.get("last_updated"),
            "file_type": m.get("file_type"),
            "chars": len(d["content"]),
        })
    return docs


def save_upload(filename: str, data: bytes, product_area: str) -> dict:
    """Extract an uploaded file into the database. Nothing is kept on disk.

    Text extraction needs a real path (pypdf, python-docx and openpyxl all
    open files), so the bytes go to a temp file that is deleted in the
    finally block. The document row is the artefact; there is no inbox
    directory to fall out of step with the database.
    """
    filename = safe_filename(filename)
    if not filename:
        raise ValueError("Filename is empty after sanitising.")
    if not Path(filename).suffix:
        filename += ".txt"

    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem
    article_id = re.sub(r"[^A-Z0-9\-_]+", "-", stem.upper()).strip("-") or "DOC"
    today = datetime.now().strftime("%Y-%m-%d")

    scratch = Path(tempfile.mkdtemp(prefix="clouddesk_upload_"))
    file_path = scratch / filename

    try:
        file_path.write_bytes(data)

        extracted = extract_text_from_file(file_path)
        if not extracted.strip():
            raise ExtractionError("The file contains no text.")

        # A hand-written text file may carry its own frontmatter; when it
        # does that wins, and the derived values only fill the gaps.
        existing, body = split_frontmatter(
            extracted, allow=ext in TEXT_FRONTMATTER_EXTS
        )

        meta = {
            "source_file": filename,
            "article_id": article_id,
            "product_area": product_area,
            "last_updated": today,
            "title": derive_title(file_path, body or extracted),
            "file_type": ext.lstrip("."),
        }
        meta.update({k: v for k, v in existing.items() if v not in (None, "")})

        content = (body or extracted).strip()
        db.upsert_document({"content": content, "metadata": meta})

        return {
            "filename": filename,
            "article_id": meta["article_id"],
            "product_area": meta["product_area"],
            "title": meta["title"],
            "file_type": ext.lstrip("."),
            "chars": len(content),
            "bytes": len(data),
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


WEEK5_DIR = Path(SRC_DIR).parent / "week5"


def week5_artefacts():
    """The Week 5 write-up, served from the files that are the deliverable.

    The markdown is the single source of truth — the UI renders it rather
    than holding a second copy that could drift from what is submitted.
    """
    import tracing

    def read(name):
        path = WEEK5_DIR / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    sample = {}
    sample_path = WEEK5_DIR / "sample.json"
    if sample_path.exists():
        sample = json.loads(sample_path.read_text(encoding="utf-8"))

    traces = tracing.load_traces()
    by_id = {t["trace_id"]: t for t in traces}

    # Enough per sampled trace to render a list without shipping all 99.
    summaries = []
    for trace_id in sample.get("trace_ids", []):
        trace = by_id.get(trace_id)
        if not trace:
            continue
        outcome = trace["outcome"]
        summaries.append({
            "trace_id": trace_id,
            "query": trace["query"],
            "refused": outcome["refused"],
            "refusal_reason": outcome["refusal_reason"],
            "answer": outcome["answer"],
            "raw_output": trace["generation"].get("raw_output"),
            "best_distance": outcome.get("best_distance"),
            "mode": trace["request"].get("retrieval_mode"),
            "top_k": trace["request"].get("top_k"),
        })

    return {
        "sample": sample,
        "traces": summaries,
        "trace_count": len(traces),
        "taxonomy_md": read("taxonomy.md"),
        "notes_md": read("notes.md"),
        "prediction_md": read("prediction.md"),
        "benchmark_md": read("benchmark_note.md"),
        "replay_md": read("replay_evidence.md"),
    }


def week6_module():
    """experiments/week6_eval.py holds the Week 6 logic; the UI calls it, never copies it."""
    experiments = str(Path(SRC_DIR).parent / "experiments")
    if experiments not in sys.path:
        sys.path.insert(0, experiments)
    import week6_eval
    return week6_eval


def week8_module():
    """experiments/week8_trajectory_eval.py holds the Week 8 logic; the UI reads it."""
    experiments = str(Path(SRC_DIR).parent / "experiments")
    if experiments not in sys.path:
        sys.path.insert(0, experiments)
    import week8_trajectory_eval
    return week8_trajectory_eval


def week9_module():
    """experiments/week9_ui.py reads week9/ and runs live discovery for the UI."""
    experiments = str(Path(SRC_DIR).parent / "experiments")
    if experiments not in sys.path:
        sys.path.insert(0, experiments)
    import week9_ui
    return week9_ui


def week7_module():
    """experiments/week7_ui.py reads week7/ and runs one ticket live for the UI."""
    experiments = str(Path(SRC_DIR).parent / "experiments")
    if experiments not in sys.path:
        sys.path.insert(0, experiments)
    import week7_ui
    return week7_ui


def week10_module():
    """experiments/week10_ui.py reads week10/ and runs one question live for the UI."""
    experiments = str(Path(SRC_DIR).parent / "experiments")
    if experiments not in sys.path:
        sys.path.insert(0, experiments)
    import week10_ui
    return week10_ui


ROOT_DIR = Path(SRC_DIR).parent

# The markdown deliverables the Reports view renders. Paths are relative to
# the repo root; the UI shows the path next to each document so a reviewer
# can open the same file.
REPORT_FILES = [
    ("demo_guide", "Demo guide (Weeks 3–9)", "DEMO_GUIDE.md"),
    ("readme", "README", "README.md"),
    ("results", "Week 4 report (results.md)", "results.md"),
    ("demo_guide_w34", "Weeks 3–4 deep-dive guide", "DEMO_GUIDE_WEEKS_3_4.md"),
    ("week5", "Week 5 README", "week5/README.md"),
    ("week6", "Week 6 README", "week6/README.md"),
    ("week6_demo", "Week 6 demo notes", "week6/DEMO.md"),
    ("week7", "Week 7 README", "week7/README.md"),
    ("week7_demo", "Week 7 demo notes", "week7/DEMO.md"),
    ("week8", "Week 8 README", "week8/README.md"),
    ("week8_demo", "Week 8 demo notes", "week8/DEMO.md"),
    ("week9", "Week 9 README", "week9/README.md"),
    ("week9_demo", "Week 9 demo notes", "week9/DEMO.md"),
    ("week10", "Week 10 README", "week10/README.md"),
    ("week10_report", "Week 10 report", "week10/REPORT.md"),
    ("database", "Database notes", "DATABASE.md"),
    ("setup_db", "Postgres setup", "SETUP_DB.md"),
]


def reports():
    docs = []
    for key, title, rel in REPORT_FILES:
        path = ROOT_DIR / rel
        docs.append({"key": key, "title": title, "path": rel,
                     "exists": path.exists(),
                     "text": path.read_text(encoding="utf-8") if path.exists() else None,
                     "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
                     if path.exists() else None})
    return {"docs": docs}


def week8_rescore():
    """Rescore every stored trajectory run from its raw transcripts, in memory.

    Scoring never needs the model (week8_trajectory_eval separates `run` from
    `report`), so this is cheap. Nothing is written: the result is compared
    against week8/report.json so the UI can say whether the committed numbers
    reproduce from the raw runs on this machine.
    """
    w8 = week8_module()
    stored = json.loads(w8.REPORT_JSON.read_text(encoding="utf-8")) if w8.REPORT_JSON.exists() else {}
    out = {"rescored_at": datetime.now().isoformat(timespec="seconds"), "configs": {}}
    for config in list(w8.CONFIGS) + w8.HISTORICAL:
        path, scored = w8.scored_config(config)
        if not scored:
            continue
        summary = w8.summarise(scored)
        was = (stored.get("configs") or {}).get(config, {}).get("summary") or {}
        keys = ["outcome_pass_rate_pct", "trajectory_pass_rate_pct", "gap_pp",
                "tool_choice_accuracy_pct", "argument_validity_pct", "step_efficiency_mean",
                "cost_p50_usd", "cost_max_usd"]
        out["configs"][config] = {
            "run": path.relative_to(w8.ROOT).as_posix(),
            "summary": {k: summary.get(k) for k in keys},
            "stored": {k: was.get(k) for k in keys},
            "matches_report": all(summary.get(k) == was.get(k) for k in keys),
            "tickets": [{"ticket_id": s["ticket_id"], "outcome_pass": s["outcome_pass"],
                         "trajectory_pass": s["trajectory_pass"], "path": s["path"]} for s in scored],
        }
    out["all_match"] = all(c["matches_report"] for c in out["configs"].values()) if out["configs"] else False
    return out


def week9_wire_capture():
    """initialize -> initialized -> tools/list -> tools/call against server two, live.

    The same four steps week9/wire.json annotates, replayed now over stdio
    with the host's own client; the raw messages come back unformatted and
    nothing is written to week9/. No model runs: the tools/call is fixed.
    """
    from mcp_client import MCPConnection, load_config
    w9 = week9_module()
    spec = load_config(w9.W9 / "mcp_config.json")["ticket-history"]
    wire = []
    connection = MCPConnection("ticket-history", spec, root=str(w9.ROOT), wire=wire)
    try:
        init = connection.connect()
        tools = connection.list_tools()
        result = connection.call_tool("lookup_ticket", {"ticket_id": "TCK-4471"})
    finally:
        connection.close()
    return {"server": "ticket-history", "transport": connection.transport,
            "protocol_version": init.get("protocolVersion"), "server_info": init.get("serverInfo"),
            "tool_count": len(tools), "tools": sorted(t["name"] for t in tools),
            "call": {"tool": "lookup_ticket", "arguments": {"ticket_id": "TCK-4471"},
                     "is_error": bool(result.get("isError")),
                     "text": "\n".join(c.get("text", "") for c in result.get("content", []))},
            "messages": wire,
            "model_call": "none — the model only ever runs in the host between tools/list and "
                          "tools/call; this capture fixed the call so no model was needed"}


# ---------------------------------------------------------------------------
# one expensive action at a time
# ---------------------------------------------------------------------------

RUN_LOCK = threading.Lock()
RUN_STATE = {"name": None, "started": None}


class Busy(Exception):
    pass


class exclusive:
    """Hold the model/index lock for one action, or refuse at once with 409."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        if not RUN_LOCK.acquire(blocking=False):
            raise Busy(f"another run is in progress: {RUN_STATE['name']} "
                       f"(started {RUN_STATE['started']}). Wait for it to finish.")
        RUN_STATE.update(name=self.name, started=datetime.now().isoformat(timespec="seconds"))
        return self

    def __exit__(self, *exc):
        RUN_STATE.update(name=None, started=None)
        RUN_LOCK.release()
        return False


def runs_status():
    return {"running": RUN_STATE["name"], "started": RUN_STATE["started"]}


def health():
    import tracing
    reachable = False
    try:
        reachable = db.server_reachable(db.DATABASE_URL)
    except Exception:
        reachable = False
    return {
        "ok": reachable,
        "store": db.safe_url(db.DATABASE_URL),
        "store_reachable": reachable,
        "indexes": index_status() if reachable else {},
        "build": tracing.app_version(),
        "models": {
            "embedding": "sentence-transformers/all-MiniLM-L6-v2",
            "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "chat_generator": os.environ.get("RAG_LLM", "Qwen/Qwen2.5-0.5B-Instruct"),
            "ticket_agent": os.environ.get("RAG_AGENT_LLM", "Qwen/Qwen2.5-1.5B-Instruct"),
            "judge": os.environ.get("RAG_JUDGE_MODEL", "HuggingFaceTB/SmolLM2-1.7B-Instruct"),
        },
        "tracing": {"jsonl": tracing.TRACING_ENABLED, "langfuse": tracing.langfuse_enabled()},
        **runs_status(),
    }


def run_retrieval(query, active_query, top_k, strategy, where):
    dense = retrieve_chunks(active_query, top_k=top_k, strategy=strategy, where=where)
    bm25 = retrieve_bm25(active_query, top_k=top_k, strategy=strategy, where=where)
    fused = reciprocal_rank_fusion(dense, bm25, k_constant=60)[:top_k]
    reranked = rerank_chunks(query, fused, top_k=top_k)
    return {"dense": dense, "bm25": bm25, "fused": fused, "reranked": reranked}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class RAGRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter console
        # send_error() logs ("code %d, message %s", 404, ...): the first
        # argument is then an int, and `in` on it raised, which aborted every
        # 404 (a browser's /favicon.ico probe) before the response was written.
        first = args[0] if args else ""
        if isinstance(first, str) and "/api/" in first:
            super().log_message(fmt, *args)

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path: Path):
        if not path.exists():
            self.send_error(500, f"UI file missing: {path}")
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ---- GET --------------------------------------------------------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path in ("/", "/index.html"):
                self._send_html(INDEX_HTML)
            elif parsed.path == "/api/documents":
                self._send_json(document_list())
            elif parsed.path == "/api/status":
                self._send_json(index_status())
            elif parsed.path == "/api/week5":
                self._send_json(week5_artefacts())
            elif parsed.path == "/api/week6":
                self._send_json(week6_module().ui_artefacts())
            elif parsed.path == "/api/week7":
                self._send_json(week7_module().ui_artefacts())
            elif parsed.path == "/api/week8":
                self._send_json(week8_module().ui_artefacts())
            elif parsed.path == "/api/week9":
                self._send_json(week9_module().ui_artefacts())
            elif parsed.path == "/api/week10":
                self._send_json(week10_module().ui_artefacts())
            elif parsed.path == "/api/week10/status":
                self._send_json({**week10_module().status(), **runs_status()})
            elif parsed.path == "/api/reports":
                self._send_json(reports())
            elif parsed.path == "/api/health":
                self._send_json(health())
            elif parsed.path == "/api/runs":
                self._send_json(runs_status())
            elif parsed.path == "/api/golden":
                self._send_json({
                    "questions": golden_eval.load_golden_set(),
                    "k": golden_eval.DEFAULT_K,
                    "rrf_k": golden_eval.RRF_K,
                    "candidate_k": golden_eval.CANDIDATE_K,
                    "arms": golden_eval.ARM_LABELS,
                })
            else:
                self.send_error(404, "Not Found")
        except Exception as e:
            traceback.print_exc()
            self._send_json({"status": "error", "error": str(e)}, status=500)

    # ---- POST -------------------------------------------------------------

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(content_length)
        try:
            payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
        except json.JSONDecodeError:
            self._send_json({"status": "error", "error": "Body must be JSON"}, status=400)
            return

        try:
            if parsed.path == "/api/upload":
                with exclusive("upload + re-index"):
                    self._handle_upload(payload)
            elif parsed.path == "/api/trace":
                self._handle_trace(payload)
            elif parsed.path == "/api/week6/run":
                judge = "auto" if payload.get("judge") == "auto" else "none"
                with exclusive("week 6 eval" + (" + judge" if judge == "auto" else " (assertions)")):
                    self._send_json({"status": "ok", **week6_module().ui_run(judge)})
            elif parsed.path == "/api/week7/workflow":
                with exclusive(f"week 7 workflow on {payload.get('ticket_id')}"):
                    self._send_json({"status": "ok",
                                     **week7_module().run_workflow(payload.get("ticket_id"))})
            elif parsed.path == "/api/week7/agent":
                with exclusive(f"week 7 agent on {payload.get('ticket_id')}"):
                    self._send_json({"status": "ok", **week7_module().run_agent(
                        payload.get("ticket_id"),
                        max_iterations=payload.get("max_iterations", 4),
                        max_tokens=payload.get("max_tokens", 4000),
                        max_cost_usd=payload.get("max_cost_usd", 0.01),
                        max_wall_seconds=payload.get("max_wall_seconds", 120.0),
                        evidence_guard=payload.get("evidence_guard", True))})
            elif parsed.path == "/api/week8/rescore":
                self._send_json({"status": "ok", **week8_rescore()})
            elif parsed.path == "/api/week9/discover":
                which = payload.get("which") if payload.get("which") in (
                    "server_one", "server_two", "gateway") else "server_two"
                with exclusive(f"week 9 discovery ({which})"):
                    self._send_json({"status": "ok", **week9_module().discover(which)})
            elif parsed.path == "/api/week9/wire":
                with exclusive("week 9 wire capture"):
                    self._send_json({"status": "ok", **week9_wire_capture()})
            elif parsed.path == "/api/week9/chat":
                from mcp_chat import handle_mcp_chat
                which = payload.get("which", "server_two")
                query = payload.get("query", "")
                top_k = payload.get("top_k", 3)
                with exclusive("week 9 mcp customer chat"):
                    self._send_json(handle_mcp_chat(query=query, which_config=which, top_k=top_k))
            elif parsed.path == "/api/week10/chat":
                mode = payload.get("mode") or "single"
                fault = payload.get("inject_fault") or None
                with exclusive(f"week 10 {mode}-agent chat" + (f" (fault: {fault})" if fault else "")):
                    result = week10_module().run_chat(mode, payload.get("message"),
                                                      payload.get("ticket"), inject_fault=fault)
                # A reply written after a specialist failed is still safe (a
                # routed hand-off), but the service was degraded: say so in the
                # status code as well as the body, so a client cannot mistake
                # it for a normal answer.
                result.pop("status", None)          # the API's status is the contract below
                if result.get("degraded"):
                    self._send_json({**result, "status": "degraded",
                                     "error": "a specialist was unavailable; the ticket was routed "
                                              "to a person instead of being answered"}, status=503)
                else:
                    self._send_json({**result, "status": "ok"})
            elif parsed.path == "/api/week5/rerun":
                from week5_rerun import rerun_sample
                with exclusive("week 5 rerun of the 20 sampled questions"):
                    self._send_json({"status": "ok",
                                     **rerun_sample(retrieval_mode=payload.get("mode") or None)})
            elif parsed.path == "/api/golden/evaluate":
                with exclusive("golden set evaluation"):
                    self._handle_golden(payload)
            elif parsed.path == "/api/reindex":
                with exclusive("re-index"):
                    self._send_json({"status": "ok", "chunks": rebuild_indexes()})
            elif parsed.path == "/api/documents/delete":
                with exclusive("delete + re-index"):
                    self._handle_delete(payload)
            elif parsed.path in ("/api/ask", "/api/inspect"):
                with exclusive("chat question"):
                    self._handle_query(parsed.path, payload)
            else:
                self.send_error(404, "Unknown API route")
        except Busy as e:
            self._send_json({"status": "error", "error": str(e), **runs_status()}, status=409)
        except ExtractionError as e:
            self._send_json({"status": "error", "error": str(e)}, status=415)
        except ValueError as e:
            self._send_json({"status": "error", "error": str(e)}, status=400)
        except Exception as e:
            traceback.print_exc()
            self._send_json({"status": "error", "error": str(e)}, status=500)

    def _handle_upload(self, payload):
        filename = (payload.get("filename") or "").strip()
        product_area = (payload.get("product_area") or "Custom Upload").strip() or "Custom Upload"
        reindex = bool(payload.get("reindex", True))

        if payload.get("content_b64"):
            data = base64.b64decode(payload["content_b64"])
        else:
            data = (payload.get("content") or "").strip().encode("utf-8")

        if not filename or not data:
            raise ValueError("Missing filename or content")

        info = save_upload(filename, data, product_area)
        info["status"] = "ok"
        info["chunks"] = rebuild_indexes().get(DEFAULT_STRATEGY) if reindex else None
        self._send_json(info)

    def _handle_trace(self, payload):
        trace_id = (payload.get("trace_id") or "").strip()
        if not trace_id:
            raise ValueError("Missing trace_id")

        import tracing
        trace = tracing.get_trace(trace_id)
        if trace is None:
            self._send_json(
                {"status": "error", "error": f"No trace {trace_id}"}, status=404
            )
            return
        self._send_json({"status": "ok", "trace": trace})

    def _handle_golden(self, payload):
        try:
            k = int(payload.get("k", golden_eval.DEFAULT_K))
        except (TypeError, ValueError):
            k = golden_eval.DEFAULT_K
        k = max(1, min(k, 10))

        strategy = payload.get("strategy") or DEFAULT_STRATEGY
        if strategy not in STRATEGIES:
            raise ValueError(f"Unknown strategy: {strategy}")

        report = golden_eval.evaluate(k=k, strategy=strategy)
        report["status"] = "ok"
        self._send_json(report)

    def _handle_delete(self, payload):
        name = safe_filename(payload.get("source_file") or "")
        if not name:
            raise ValueError("Missing source_file")

        removed = db.delete_document(name)     # chunks cascade
        if not removed:
            self._send_json(
                {"status": "error", "error": f"{name} not found"}, status=404
            )
            return

        self._send_json({"status": "ok", "deleted": name,
                         "chunks": rebuild_indexes()})

    def _handle_query(self, path, payload):
        query = (payload.get("query") or "").strip()
        if not query:
            raise ValueError("Query is empty")
        top_k = int(payload.get("top_k", 5))
        strategy = payload.get("strategy", DEFAULT_STRATEGY)
        product_area = payload.get("product_area") or None
        article_id = payload.get("article_id") or None
        mode = payload.get("mode", "hybrid_rerank")

        # The distance gate is calibrated against this corpus's prose.
        # Record-style documents (directories, exports) sit far outside
        # that distribution, so the threshold is adjustable per query
        # rather than hard-coded.
        try:
            threshold = float(payload.get("threshold", RELEVANCE_THRESHOLD))
        except (TypeError, ValueError):
            threshold = RELEVANCE_THRESHOLD

        threshold = max(0.05, min(threshold, 2.0))

        where = build_where(product_area=product_area, article_id=article_id)

        active_query = query
        rewritten_query = None
        hyde_doc = None
        if payload.get("rewrite"):
            rewritten_query = rewrite_query(query)
            active_query = rewritten_query
        if payload.get("hyde"):
            hyde_doc = generate_hypothetical_document(query)
            active_query = hyde_doc

        stages = run_retrieval(query, active_query, top_k, strategy, where)
        generation = generate_answer(
            query=query,
            top_k=top_k,
            strategy=strategy,
            product_area=product_area,
            article_id=article_id,
            threshold=threshold,
            retrieval_mode=mode,
            source="ui",
        )

        inspection = {
            "query": query,
            "active_query": active_query,
            "rewritten_query": rewritten_query,
            "hyde_doc": hyde_doc,
            "mode": mode,
            "threshold": threshold,
            "strategy": strategy,
            **stages,
        }

        if path == "/api/inspect":
            self._send_json({**inspection, "generation": generation})
        else:
            generation["inspection"] = inspection
            self._send_json(generation)


class StudioServer(http.server.ThreadingHTTPServer):
    """Threaded so the evidence views answer while a model run holds RUN_LOCK."""
    allow_reuse_address = True
    daemon_threads = True


def run_server(port: int = 8000, open_browser: bool = True, warm: bool = False):
    httpd = StudioServer(("", port), RAGRequestHandler)
    url = f"http://localhost:{port}"

    print("=" * 72)
    print("      CLOUDDESK KNOWLEDGE STUDIO — WEB UI")
    print("=" * 72)
    if warm:
        # Opt-in: load the ticket drafter and the retrievers now, through the
        # same caches every request uses, so the first browser question does
        # not pay ~70 s for them. Costs that time at startup instead.
        print("  Warming the local models (ticket drafter, embedder, reranker)…")
        seconds = week10_module().warm_models()
        print(f"  Models loaded in {seconds:.0f} s.")
    print(f"  Server running locally at: {url}")
    print(f"  Store: {db.DATABASE_URL}")
    print("  Press Ctrl+C to stop the server.")
    print("=" * 72 + "\n")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    run_server(port=port, open_browser=False)
