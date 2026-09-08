"""CloudDesk help centre RAG application — Enhanced with Week 4 Hybrid & Debugging Features.

Usage
-----
    python main.py ingest
    python main.py ask "How long is a password reset link valid?"
    python main.py inspect "What should I do if my payment fails in HC-003?"
    python main.py hybrid-search "payment failed HC-003" --rerank
    python main.py rewrite "uh hey why is the upload button not clickable?"
    python main.py hyde "How long is a reset link valid?"
    python main.py failure-report
    python main.py evaluate-hybrid
"""

import argparse
import os
import sys

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
)

from chunking import STRATEGIES
from vector_store import (
    build_all_indexes,
    DEFAULT_STRATEGY,
    index_counts,
)


# ============================================================
# Commands
# ============================================================

def command_ingest(args):
    """Re-chunk and re-embed every document already in the database."""
    import db

    print("=" * 72)
    print("RE-INDEXING DOCUMENTS FROM THE DATABASE")
    print("=" * 72)
    print(f"store: {db.DATABASE_URL}")
    print(
        f"chunk_size={args.chunk_size} "
        f"chunk_overlap={args.chunk_overlap}\n"
    )

    if db.stats().get("documents", 0) == 0:
        print("  No documents in the database. Add some first:")
        print("    python main.py import <file-or-directory>")
        return

    build_all_indexes(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap
    )

    print(f"\nDefault index for queries: {DEFAULT_STRATEGY}")


def command_import(args):
    """Read files from disk into the database, then re-index."""
    import db
    from pathlib import Path

    source = Path(args.path)
    if not source.exists():
        print(f"No such path: {source}")
        return

    print("=" * 72)
    print(f"IMPORTING {source}")
    print("=" * 72)

    if source.is_dir():
        imported, skipped = db.import_directory(source, args.area)
    else:
        imported, skipped = [db.import_file(source, args.area)], []

    for metadata in imported:
        print(f"  + {metadata['source_file']:<28} {metadata['article_id']}")
    for name, reason in skipped:
        print(f"  - {name:<28} skipped: {reason}")

    print(f"\n{len(imported)} imported, {len(skipped)} skipped")

    if imported and not args.no_reindex:
        print("\nRe-indexing…")
        build_all_indexes()


def command_db(args):
    """Create, inspect, reset or move the database."""
    import db
    import subprocess

    action = args.db_action
    url = args.url or db.DATABASE_URL

    if action == "url":
        print(db.safe_url(url))
        parts = db.parse_url(url)
        print(f"  host     : {parts['host']}:{parts['port']}")
        print(f"  database : {parts['database']}")
        print(f"  user     : {parts['user']}")
        print("\nOverride it per run:")
        print("  DATABASE_URL=postgresql://user@host:5432/name python main.py status")
        print("or put DATABASE_URL=... in a .env file at the project root.")
        return

    if action == "setup":
        print("=" * 72)
        print("DATABASE SETUP")
        print("=" * 72)
        try:
            steps = db.setup(url)
        except ConnectionError as error:
            print(f"  {error}")
            return
        print(f"  url             : {steps['url']}")
        print(f"  database created: {steps['created_database']} "
              f"{'(it already existed)' if not steps['created_database'] else ''}")
        print(f"  pgvector        : {steps['pgvector']}")
        print(f"  tables          : documents, chunks")
        print(f"  contents        : {steps['stats']}")
        if steps["stats"]["documents"] == 0:
            print("\n  Empty. Load documents with:")
            print("    python main.py import <file-or-folder>")
        return

    if action == "status":
        parts = db.parse_url(url)
        print(f"  url       : {db.safe_url(url)}")
        print(f"  reachable : {db.server_reachable(url)}")
        print(f"  exists    : {db.database_exists(url)}")
        if db.database_exists(url):
            print(f"  contents  : {db.stats(url)}")
        return

    if action == "reset":
        parts = db.parse_url(url)
        answer = input(
            f"Drop ALL documents and chunks in {parts['database']} "
            f"at {parts['host']}:{parts['port']}? Type the database name: "
        )
        if answer.strip() != parts["database"]:
            print("  Name did not match — nothing was changed.")
            return
        db.reset(url, confirm=True)
        print("  Tables dropped and recreated, empty.")
        return

    if action in ("backup", "restore"):
        parts = db.parse_url(url)
        path = args.file
        command = ["pg_dump" if action == "backup" else "psql",
                   "-h", parts["host"], "-p", str(parts["port"])]
        if parts["user"]:
            command += ["-U", parts["user"]]
        command += ["-d", parts["database"]]
        command += ["-f", path]

        print(f"  {' '.join(command)}")
        result = subprocess.run(command)
        if result.returncode == 0:
            print(f"  {'Wrote' if action == 'backup' else 'Restored from'} {path}")
        else:
            print(f"  {action} failed (exit {result.returncode})")
        return


def command_export(args):
    """Write every document in the database back out as files."""
    import db

    written = db.export_documents(args.to)
    print(f"Exported {len(written)} documents to {args.to}/")
    for name in written:
        print(f"  {name}")


def command_search(args):
    from retriever import retrieve_chunks, build_where, print_results

    where = build_where(
        product_area=args.area,
        article_id=args.article
    )

    print("=" * 72)
    print(f"DENSE SEARCH: {args.query}")
    if where:
        print(f"FILTER: {where}")
    print("=" * 72)

    print_results(
        retrieve_chunks(
            args.query,
            top_k=args.top_k,
            strategy=args.strategy,
            where=where
        )
    )


def command_hybrid_search(args):
    from hybrid_retriever import retrieve_hybrid
    from retriever import build_where

    where = build_where(product_area=args.area, article_id=args.article)

    mode_label = "HYBRID + CROSS-ENCODER RERANKED" if args.rerank else "HYBRID RRF"
    print("=" * 72)
    print(f"{mode_label} SEARCH: {args.query}")
    if where:
        print(f"FILTER: {where}")
    print("=" * 72)

    results = retrieve_hybrid(
        query=args.query,
        top_k=args.top_k,
        strategy=args.strategy,
        where=where,
        rerank=args.rerank
    )

    for r in results:
        score_info = (
            f"rerank={r.get('rerank_score', 0):.4f}"
            if args.rerank
            else f"rrf={r.get('rrf_score', 0):.4f} (dense={r.get('dense_rank')}, bm25={r.get('bm25_rank')})"
        )
        print(f"  {r['rank']}. [{r['chunk_id']}] {r['product_area']} | {score_info}")
        preview = r["content"].replace("\n", " ")[:110]
        print(f"     {preview}...")


def command_inspect(args):
    from inspector import inspect_query

    inspect_query(
        query=args.query,
        top_k=args.top_k,
        strategy=args.strategy,
        product_area=args.area,
        article_id=args.article,
        apply_rewrite=args.rewrite,
        apply_hyde=args.hyde
    )


def command_ask(args):
    from generator import generate_answer, print_answer

    result = generate_answer(
        args.query,
        top_k=args.top_k,
        strategy=args.strategy,
        product_area=args.area,
        article_id=args.article
    )

    print_answer(result, show_retrieved=args.show_retrieved)


def command_rewrite(args):
    from query_transform import rewrite_query
    print("=" * 72)
    print(f"ORIGINAL QUERY  : {args.query}")
    rewritten = rewrite_query(args.query)
    print(f"REWRITTEN QUERY : {rewritten}")
    print("=" * 72)


def command_hyde(args):
    from query_transform import generate_hypothetical_document
    print("=" * 72)
    print(f"USER QUESTION: {args.query}")
    print("-" * 72)
    hypo_doc = generate_hypothetical_document(args.query)
    print(f"HYDE HYPOTHETICAL DOCUMENT:\n{hypo_doc}")
    print("=" * 72)


def command_failure_report(args):
    from failure_analysis import run_failure_analysis_report
    run_failure_analysis_report(retrieval_mode=args.mode)


def command_evaluate_hybrid(args):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments"))
    from evaluate_hybrid_reranking import run_benchmark
    run_benchmark()


def command_demo(args):
    from generator import generate_answer, print_answer
    from retriever import retrieve_chunks, build_where, print_results
    from hybrid_retriever import retrieve_hybrid

    print("#" * 72)
    print("# 1. GROUNDED ANSWERS WITH CITATIONS")
    print("#" * 72)

    for query in [
        "How long is a CloudDesk password reset link valid?",
        "Why is the Upload File button unavailable in CloudDesk?",
    ]:
        print_answer(generate_answer(query))

    print("\n\n" + "#" * 72)
    print("# 2. REFUSALS (no answer exists in the corpus)")
    print("#" * 72)

    for query in [
        "What is the maximum file size limit for CloudDesk uploads in megabytes?",
        "How much does the CloudDesk Enterprise plan cost per user?",
        "What is the capital of France?",
    ]:
        print_answer(generate_answer(query))

    print("\n\n" + "#" * 72)
    print("# 3. HYBRID RETRIEVAL & EXACT KEYWORD MATCHING")
    print("#" * 72)
    exact_q = "HC-003 error 403 payment"
    print(f"Query: '{exact_q}'")
    hybrid_res = retrieve_hybrid(exact_q, top_k=3, rerank=True)
    for r in hybrid_res:
        print(f"  Rank {r['rank']}: [{r['chunk_id']}] rerank={r.get('rerank_score'):.4f} | {r['section']}")


def command_status(args):
    import db

    print("=" * 72)
    print("INDEX STATUS")
    print("=" * 72)
    print(f"  store: {db.DATABASE_URL}\n")

    counts = index_counts()

    for strategy in STRATEGIES:
        count = counts.get(strategy)
        if count:
            marker = " (default)" if strategy == DEFAULT_STRATEGY else ""
            print(f"  {strategy:<24} {count:>5} chunks{marker}")
        else:
            print(f"  {strategy:<24}     - not built "
                  f"(run: python main.py ingest)")

    documents = db.stats().get("documents", 0)
    print(f"\n  {documents} documents")


def command_ui(args):
    from server import run_server
    run_server(port=args.port, open_browser=not args.no_browser)


# ============================================================
# CLI
# ============================================================

def build_parser():
    parser = argparse.ArgumentParser(
        description="CloudDesk help centre RAG application"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ui
    ui = subparsers.add_parser(
        "ui",
        help="launch interactive web UI and visual studio in your browser"
    )
    ui.add_argument("--port", type=int, default=8000, help="server port (default: 8000)")
    ui.add_argument("--no-browser", action="store_true", help="do not auto-open browser")
    ui.set_defaults(func=command_ui)

    # ingest
    ingest = subparsers.add_parser(
        "ingest",
        help="re-chunk and re-embed the documents already in the database"
    )
    ingest.add_argument("--chunk-size", type=int, default=300)
    ingest.add_argument("--chunk-overlap", type=int, default=50)
    ingest.set_defaults(func=command_ingest)

    # import
    import_cmd = subparsers.add_parser(
        "import",
        help="read a file or directory into the database"
    )
    import_cmd.add_argument("path")
    import_cmd.add_argument("--area", default=None, help="product_area to tag")
    import_cmd.add_argument("--no-reindex", action="store_true")
    import_cmd.set_defaults(func=command_import)

    # export
    export_cmd = subparsers.add_parser(
        "export",
        help="write every document back out as files"
    )
    export_cmd.add_argument("--to", default="export")
    export_cmd.set_defaults(func=command_export)

    # db
    db_cmd = subparsers.add_parser(
        "db",
        help="create, inspect, reset, back up or move the database"
    )
    db_cmd.add_argument(
        "--url",
        default=None,
        help="connection URL to act on (default: $DATABASE_URL or .env)"
    )
    db_actions = db_cmd.add_subparsers(dest="db_action", required=True)

    db_actions.add_parser("setup", help="create the database, extension and tables")
    db_actions.add_parser("status", help="is it reachable, does it exist, what is in it")
    db_actions.add_parser("url", help="show the connection URL in use")
    db_actions.add_parser("reset", help="drop and recreate the tables (destructive)")

    db_backup = db_actions.add_parser("backup", help="pg_dump to a file")
    db_backup.add_argument("--file", default="clouddesk_rag.sql")

    db_restore = db_actions.add_parser("restore", help="psql restore from a file")
    db_restore.add_argument("--file", default="clouddesk_rag.sql")

    db_cmd.set_defaults(func=command_db)

    # status
    status = subparsers.add_parser(
        "status",
        help="show which indexes exist"
    )
    status.set_defaults(func=command_status)

    # search
    search = subparsers.add_parser(
        "search",
        help="retrieve chunks with dense semantic search"
    )
    search.add_argument("query")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument(
        "--strategy",
        choices=sorted(STRATEGIES),
        default=DEFAULT_STRATEGY
    )
    search.add_argument("--area", default=None, help="product_area filter")
    search.add_argument("--article", default=None, help="article_id filter")
    search.set_defaults(func=command_search)

    # hybrid-search
    hybrid = subparsers.add_parser(
        "hybrid-search",
        help="retrieve chunks using dense + BM25 with RRF"
    )
    hybrid.add_argument("query")
    hybrid.add_argument("--top-k", type=int, default=5)
    hybrid.add_argument("--strategy", choices=sorted(STRATEGIES), default=DEFAULT_STRATEGY)
    hybrid.add_argument("--area", default=None, help="product_area filter")
    hybrid.add_argument("--article", default=None, help="article_id filter")
    hybrid.add_argument("--rerank", action="store_true", help="apply cross-encoder reranker")
    hybrid.set_defaults(func=command_hybrid_search)

    # inspect
    inspect_cmd = subparsers.add_parser(
        "inspect",
        help="side-by-side diagnostic inspection view of query, multi-stage retrieval, and answer"
    )
    inspect_cmd.add_argument("query")
    inspect_cmd.add_argument("--top-k", type=int, default=5)
    inspect_cmd.add_argument("--strategy", choices=sorted(STRATEGIES), default=DEFAULT_STRATEGY)
    inspect_cmd.add_argument("--area", default=None, help="product_area filter")
    inspect_cmd.add_argument("--article", default=None, help="article_id filter")
    inspect_cmd.add_argument("--rewrite", action="store_true", help="apply query rewriting")
    inspect_cmd.add_argument("--hyde", action="store_true", help="apply HyDE generation")
    inspect_cmd.set_defaults(func=command_inspect)

    # ask
    ask = subparsers.add_parser(
        "ask",
        help="generate a grounded answer with citations"
    )
    ask.add_argument("query")
    ask.add_argument("--top-k", type=int, default=5)
    ask.add_argument(
        "--strategy",
        choices=sorted(STRATEGIES),
        default=DEFAULT_STRATEGY
    )
    ask.add_argument("--area", default=None, help="product_area filter")
    ask.add_argument("--article", default=None, help="article_id filter")
    ask.add_argument(
        "--show-retrieved",
        action="store_true",
        help="also print the retrieved chunks"
    )
    ask.set_defaults(func=command_ask)

    # rewrite
    rewrite_cmd = subparsers.add_parser("rewrite", help="rewrite user query for search optimization")
    rewrite_cmd.add_argument("query")
    rewrite_cmd.set_defaults(func=command_rewrite)

    # hyde
    hyde_cmd = subparsers.add_parser("hyde", help="generate hypothetical document embedding passage")
    hyde_cmd.add_argument("query")
    hyde_cmd.set_defaults(func=command_hyde)

    # failure-report
    fail_cmd = subparsers.add_parser("failure-report", help="run automated failure separation analysis")
    fail_cmd.add_argument("--mode", default="hybrid", choices=["dense", "hybrid", "hybrid_rerank"])
    fail_cmd.set_defaults(func=command_failure_report)

    # evaluate-hybrid
    eval_cmd = subparsers.add_parser("evaluate-hybrid", help="benchmark Dense vs BM25 vs Hybrid vs Reranker")
    eval_cmd.set_defaults(func=command_evaluate_hybrid)

    # demo
    demo = subparsers.add_parser(
        "demo",
        help="run the full deliverable demo"
    )
    demo.set_defaults(func=command_demo)

    return parser


if __name__ == "__main__":
    parsed = build_parser().parse_args()
    parsed.func(parsed)

