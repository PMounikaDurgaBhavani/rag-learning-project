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
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402,F401
from mcp_types import CallToolResult, TextContent  # noqa: E402

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
def get_article(article_id: str) -> CallToolResult:
    """Fetch the full text of ONE help-centre article by its exact id.

    Use this when you already know the article id. Ids are "HC-" plus three
    digits, for example HC-008. If you only have a topic, or an id written
    another way ("HC-8", "article 8"), call search_kb first and use the
    article_id it returns.

    Returns the article as markdown. If the id does not exist, the error says
    which ids do, so you can call get_article once more with a correct one.
    """
    documents = _documents()
    document = documents.get(article_id)
    if document is None:
        # Recoverable: say what was wrong, what valid input looks like, and
        # what to do next. ToolError's text reaches the model verbatim.
        # The next action goes first, and the result is returned rather than
        # raised: a raised ToolError reaches the model as "Error executing tool
        # get_article: ..." and a small model gives up on that prefix (v1 and
        # v2 of this message, week9/error_before_after.md). isError stays true.
        digits = "".join(ch for ch in str(article_id) if ch.isdigit())
        guess = f"HC-{int(digits):03d}" if digits else None
        if guess in documents:
            next_step = f"Retry now: call get_article with article_id \"{guess}\"."
        else:
            next_step = "Retry now: call search_kb with the topic, then get_article with its article_id."
        return CallToolResult(is_error=True, content=[TextContent(type="text", text=(
            f"{next_step} Reason: article {article_id!r} not found; ids are HC- plus "
            f"three digits. Known ids: {', '.join(sorted(documents))}."))])
    return CallToolResult(content=[TextContent(type="text", text=document["content"])])


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
