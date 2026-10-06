"""Server one: the CloudDesk knowledge base as an MCP server (stdio).

Exposes the Week 3-8 RAG corpus — the help-centre articles in Postgres —
as MCP primitives:

    tools      search_kb, get_article          (the model decides to call them)
    resources  kb://articles/{article_id}      (the app decides to attach them)
    prompts    answer_from_kb                  (the user picks it)

No model runs here. search_kb is BM25 retrieval and returns chunks; turning
chunks into an answer is the host's job, in the host's process, with the
host's model. A server that "answers the ticket" would hide a second model
behind a capability.

    venv/bin/python mcp_servers/kb_server.py          # speaks JSON-RPC on stdio
"""

import contextlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

server = MCPServer("clouddesk-kb")


def _quiet():
    """stdout is the JSON-RPC channel: anything the RAG code prints goes to stderr."""
    return contextlib.redirect_stdout(sys.stderr)


def _documents():
    with _quiet():
        import db
        return {doc["metadata"]["article_id"]: doc for doc in db.load_documents()}


@server.tool()
def search_kb(query: str, top_k: int = 3) -> list[dict]:
    """Search the CloudDesk help-centre articles by keyword. Returns the best
    matching passages with their article_id, product area and section."""
    with _quiet():
        from bm25_retriever import retrieve_bm25
        chunks = retrieve_bm25(query, top_k=max(1, min(int(top_k), 5)))
    return [{"article_id": c.get("article_id"), "product_area": c.get("product_area"),
             "section": c.get("section"), "text": (c.get("content") or "")[:600]}
            for c in chunks]


@server.tool()
def get_article(article_id: str) -> str:
    """Get article."""
    document = _documents().get(article_id)
    if document is None:
        # ToolError's message reaches the model verbatim; any other exception
        # is masked by the SDK as "Error executing tool get_article".
        raise ToolError("Error 3")
    return document["content"]


@server.resource("kb://articles/{article_id}", mime_type="text/markdown")
def article_resource(article_id: str) -> str:
    """One help-centre article, for the host to attach as context."""
    document = _documents().get(article_id)
    return document["content"] if document else f"No article {article_id}."


@server.prompt()
def answer_from_kb(question: str) -> str:
    """A user-selectable template: answer a customer question from the KB, citing ids."""
    return (f"Answer this customer question using only CloudDesk help-centre articles. "
            f"Search first, cite article ids like HC-003, and say so if nothing matches.\n\n"
            f"Question: {question}")


if __name__ == "__main__":
    server.run("stdio")
