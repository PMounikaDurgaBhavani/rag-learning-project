"""Web UI server for CloudDesk Knowledge Studio (RAG).

Serves the single-page UI from ``src/static/index.html`` and a small JSON API:

  GET  /                      UI
  GET  /api/documents         indexed documents (metadata + size)
  GET  /api/status            per-strategy index status
  POST /api/ask               grounded answer + retrieval stages
  POST /api/inspect           retrieval stages + grounded answer
  POST /api/upload            upload ANY file (text or base64) and index it
  POST /api/reindex           rebuild every index
  POST /api/documents/delete  remove a document and re-index
"""

import base64
import http.server
import json
import os
import re
import sys
import traceback
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
from retriever import retrieve_chunks, build_where
from bm25_retriever import retrieve_bm25, clear_bm25_cache
from hybrid_retriever import reciprocal_rank_fusion
from reranker import rerank_chunks
from query_transform import rewrite_query, generate_hypothetical_document
from loader import (
    load_articles,
    extract_text_from_file,
    meta_path_for,
    ExtractionError,
    DATA_DIR,
    META_SUFFIX,
    TEXT_FRONTMATTER_EXTS,
)
from vector_store import (
    get_client,
    collection_name,
    STRATEGIES,
    DEFAULT_STRATEGY,
    build_all_indexes,
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
    client = get_client()
    status = {}
    for strat in STRATEGIES:
        try:
            c = client.get_collection(collection_name(strat))
            status[strat] = {"count": c.count(), "status": "ready"}
        except Exception:
            status[strat] = {"count": 0, "status": "not_built"}
    return status


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
    """Persist an uploaded file into DATA_DIR and validate it is readable.

    Text-like files (.md/.txt/...) get YAML frontmatter prepended when they
    have none. Every other type is stored verbatim with a sidecar
    ``<name>.meta.json`` carrying its metadata.
    """
    DATA_DIR.mkdir(exist_ok=True)

    filename = safe_filename(filename)
    if not filename:
        raise ValueError("Filename is empty after sanitising.")
    if not Path(filename).suffix:
        filename += ".md"

    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem
    article_id = re.sub(r"[^A-Z0-9\-_]+", "-", stem.upper()).strip("-") or "DOC"
    today = datetime.now().strftime("%Y-%m-%d")
    file_path = DATA_DIR / filename
    sidecar = meta_path_for(file_path)

    if ext in TEXT_FRONTMATTER_EXTS:
        text = data.decode("utf-8", errors="ignore").lstrip("﻿").strip()
        if not text.startswith("---"):
            text = (
                f"---\n"
                f"article_id: {article_id}\n"
                f"product_area: {product_area}\n"
                f"last_updated: {today}\n"
                f"---\n\n{text}\n"
            )
        file_path.write_text(text, encoding="utf-8")
        if sidecar.exists():
            sidecar.unlink()
    else:
        file_path.write_bytes(data)
        sidecar.write_text(json.dumps({
            "article_id": article_id,
            "product_area": product_area,
            "last_updated": today,
            "title": stem,
        }, indent=2), encoding="utf-8")

    # Validate that we can actually get text out of it.
    try:
        extracted = extract_text_from_file(file_path)
        if not extracted.strip():
            raise ExtractionError("The file contains no text.")
    except ExtractionError:
        file_path.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise

    return {
        "filename": filename,
        "article_id": article_id,
        "product_area": product_area,
        "file_type": ext.lstrip("."),
        "chars": len(extracted),
        "bytes": len(data),
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
        if "/api/" in (args[0] if args else ""):
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
                self._handle_upload(payload)
            elif parsed.path == "/api/reindex":
                self._send_json({"status": "ok", "chunks": rebuild_indexes()})
            elif parsed.path == "/api/documents/delete":
                self._handle_delete(payload)
            elif parsed.path in ("/api/ask", "/api/inspect"):
                self._handle_query(parsed.path, payload)
            else:
                self.send_error(404, "Unknown API route")
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

    def _handle_delete(self, payload):
        name = safe_filename(payload.get("source_file") or "")
        if not name:
            raise ValueError("Missing source_file")
        file_path = DATA_DIR / name
        if not file_path.exists():
            self._send_json({"status": "error", "error": f"{name} not found"}, status=404)
            return
        file_path.unlink()
        meta_path_for(file_path).unlink(missing_ok=True)
        self._send_json({"status": "ok", "deleted": name, "chunks": rebuild_indexes()})

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


def run_server(port: int = 8000, open_browser: bool = True):
    http.server.HTTPServer.allow_reuse_address = True
    httpd = http.server.HTTPServer(("", port), RAGRequestHandler)
    url = f"http://localhost:{port}"

    print("=" * 72)
    print("      CLOUDDESK KNOWLEDGE STUDIO — WEB UI")
    print("=" * 72)
    print(f"  Server running locally at: {url}")
    print(f"  Documents folder: {DATA_DIR.resolve()}")
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
