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
    python main.py eval                 # Week 6: pass rate by mode
    python main.py race                 # Week 7: agent vs fixed workflow
    python main.py week11 drill         # Week 11: find the incident behind a vague complaint, timed
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


def command_eval(args):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments"))
    import week6_eval
    return week6_eval.cmd_run(args)


def command_race(args):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments"))
    import week7_race
    return week7_race.race(args)


def command_week10(args):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments"))
    import week10_benchmark
    return week10_benchmark.main(args.rest)


def command_week11(args):
    """Week 11 from the terminal: the drill, the artefacts, a controlled incident."""
    import json
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiments"))
    import week11_ui

    if args.week11_action == "drill":
        result = week11_ui.drill(args.complaint, as_of=args.as_of, procedure=args.procedure)
        print(f"Complaint : {result['complaint']}")
        print(f"Window    : {result['window']['local_start']} -> {result['window']['local_end']} "
              f"({result['window']['tz']}); used: {result['window_used']}"
              + (f"; widened: {result['widened_to']}" if result["widened_to"] else ""))
        print(f"Time to find: {result['time_to_find']} ({result['time_to_find_ms']} ms, measured, "
              f"procedure {result['procedure']})")
        for i, s in enumerate(result["slices"], 1):
            print(f"  {i}. {s['slice']:<58} hits {s['count']:>4}  {s['ms']:>8} ms  {s.get('note') or ''}")
        print(f"Matches: {result['match_count']}")
        for m in result["matches"]:
            tag = "FIXTURE" if m["fixture"] else "REAL"
            print(f"  [{tag}] {m['trace_id']}  {m['ts_local']}  {m['source']}  {m['prompt_version']}  "
                  f"{(m['ticket'] or {}).get('ticket_id')}  -> {m['policy_check']['verdict']}")
        print("Missing fields:")
        for line in result["missing_fields"]:
            print(f"  - {line}")
        return 0
    if args.week11_action == "report":
        import week11_report
        week11_report.main()
        return 0
    if args.week11_action == "plant":
        saved = week11_ui.plant_incident(args.fixture, force=args.force)
        print(json.dumps({k: saved[k] for k in ("fixture_id", "trace_id", "request_id", "planted_at",
                                                 "prompt_version_at_planting", "wrong_as_expected",
                                                 "policy_check", "already_planted") if k in saved},
                         indent=2, default=str))
        print("\nAnswer:\n" + (saved.get("answer") or ""))
        return 0
    if args.week11_action == "eval":
        record = week11_ui.run_suite(judge=args.judge, label=args.label, frozen=not args.fresh)
        if not args.fresh:
            print(record["stdout"])
        print(f"saved: {record['stdout_file']}  exit {record['exit_code']}")
        return record["exit_code"]
    return 1


def command_capstone(args):
    """Week 12: the Tier-1 deflection assistant as one application.

        python main.py capstone up        seed data, start the MCP server (HTTP), build the dashboard
                                          if missing, serve everything at http://localhost:8000/app/
        python main.py capstone seed      (re)build data/capstone/seed/*.json from the Week 6/7 artefacts
        python main.py capstone mcp       run the help-centre MCP server alone (HTTP, for other squads)
        python main.py capstone build-ui  build frontend/dist (needs node + npm)
        python main.py capstone eval ...  the integrated evaluation (experiments/week12_eval.py)
    """
    import json
    import shutil
    import signal
    import subprocess
    import time
    from pathlib import Path

    root = Path(__file__).resolve().parent
    experiments = str(root / "experiments")
    if experiments not in sys.path:
        sys.path.insert(0, experiments)
    from capstone import config as cfg
    from capstone import seed as seed_module

    action = args.capstone_action

    def ensure_seed():
        if not (cfg.SEED_DIR / "tickets.json").exists() or args.reseed:
            counts = seed_module.write()
            print(f"  demo data: {counts} -> {cfg.SEED_DIR.relative_to(root)}/")
        else:
            print(f"  demo data: present in {cfg.SEED_DIR.relative_to(root)}/ (use --reseed to rebuild)")

    def build_ui(force=False):
        dist = cfg.FRONTEND_DIST / "index.html"
        if dist.exists() and not force:
            print(f"  dashboard: built ({dist.relative_to(root)})")
            return True
        npm = shutil.which("npm")
        if not npm:
            print("  dashboard: npm not found; the API still runs, /app/ will explain how to build the UI")
            return False
        frontend = root / "frontend"
        if not (frontend / "node_modules").exists():
            print("  dashboard: npm install (first time, ~1 min)…")
            subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=frontend, check=False)
        print("  dashboard: npm run build…")
        result = subprocess.run([npm, "run", "build"], cwd=frontend, check=False)
        print(f"  dashboard: {'built' if result.returncode == 0 else 'BUILD FAILED (exit %d)' % result.returncode}")
        return result.returncode == 0

    def start_mcp():
        from capstone import mcp_session
        if mcp_session.http_server_up():
            print(f"  MCP server: already answering at {cfg.MCP_HTTP_URL}")
            return None
        log = cfg.WEEK12_DIR / "logs" / "mcp_helpcentre_server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log, "a", encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(root / "mcp_servers" / "helpcentre_server.py"),
                                 "--http", str(cfg.MCP_HTTP_PORT)], cwd=root, stdout=handle, stderr=handle)
        for _ in range(60):
            if mcp_session.http_server_up():
                break
            if proc.poll() is not None:
                print(f"  MCP server: exited early (see {log.relative_to(root)})")
                return None
            time.sleep(0.5)
        print(f"  MCP server: pid {proc.pid} at {cfg.MCP_HTTP_URL} (log {log.relative_to(root)})")
        return proc

    if action == "seed":
        args.reseed = True
        ensure_seed()
        return 0
    if action == "build-ui":
        return 0 if build_ui(force=True) else 1
    if action == "mcp":
        import runpy
        sys.argv = ["helpcentre_server.py", "--http", str(args.port or cfg.MCP_HTTP_PORT)]
        runpy.run_path(str(root / "mcp_servers" / "helpcentre_server.py"), run_name="__main__")
        return 0
    if action in ("eval", "trajectory", "taxonomy", "reopen", "cost", "sample", "report"):
        import week12_eval
        return week12_eval.main([action] + list(args.rest or []))
    if action == "up":
        import db
        print("=" * 72)
        print("      CLOUDDESK TIER-1 DEFLECTION ASSISTANT — WEEK 12")
        print("=" * 72)
        if not db.server_reachable(db.DATABASE_URL):
            print(f"  Postgres is not reachable at {db.safe_url(db.DATABASE_URL)}.")
            print("  Start it (see README 'Prerequisites'), e.g.:")
            print('    LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 pg_ctl -D ~/pgdata/clouddesk -o "-p 5433" -l ~/pgdata/clouddesk/server.log start')
            return 1
        stats = db.stats()
        print(f"  corpus: {stats.get('documents')} documents, chunks {stats.get('chunks')}")
        if (stats.get("documents") or 0) < 13:
            print("  corpus is incomplete; importing samples/helpcentre, HC-008 and samples/capstone, then re-indexing…")
            db.import_directory(root / "samples" / "helpcentre")
            db.import_file(root / "samples" / "article_08_refunds_escalation.md")
            db.import_directory(root / "samples" / "capstone")
            from vector_store import build_all_indexes
            build_all_indexes()
        ensure_seed()
        if not args.no_ui:
            build_ui()
        mcp_proc = None if args.no_mcp else start_mcp()
        from server import run_server
        try:
            run_server(port=args.port, open_browser=not args.no_browser, warm=args.warm, open_path="/app/",
                       banner="CLOUDDESK TIER-1 DEFLECTION ASSISTANT — WEEK 12")
        finally:
            if mcp_proc is not None and mcp_proc.poll() is None:
                mcp_proc.send_signal(signal.SIGTERM)
                try:
                    mcp_proc.wait(timeout=5)
                except Exception:
                    mcp_proc.kill()
                print("  MCP server stopped.")
        return 0
    return 1


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
    run_server(port=args.port, open_browser=not args.no_browser, warm=args.warm)


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
    ui.add_argument("--warm", action="store_true",
                    help="load the ticket drafter and retrievers at startup (~70 s) so the "
                         "first chat question in the browser does not pay for them")
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

    # eval (Week 6: ticket replies, pass rate by mode)
    week6 = subparsers.add_parser(
        "eval",
        help="Week 6: draft replies for every case, run assertions + judge, print pass rate by mode"
    )
    week6.add_argument("--judge", choices=["v1", "v2", "none"], default=None,
                       help="default: the newest judge the labelling protocol allows")
    week6.add_argument("--frozen", action="store_true",
                       help="reuse week6/replies_25.json instead of drafting fresh")
    week6.set_defaults(func=command_eval)

    # race (Week 7: agent vs fixed workflow over the same 10 tickets)
    race = subparsers.add_parser(
        "race",
        help="Week 7: race the refund-chase agent against the fixed workflow"
    )
    race.add_argument("--max-iterations", type=int, default=6)
    race.add_argument("--max-tokens", type=int, default=8000)
    race.add_argument("--max-cost", type=float, default=0.01)
    race.add_argument("--max-seconds", type=float, default=120.0)
    race.set_defaults(func=command_race)

    # week10 (single agent vs manager + two specialists on 10 Week 6 cases)
    week10 = subparsers.add_parser(
        "week10",
        help="Week 10: manifest | run | judge | report | failure | all "
             "(python main.py week10 all)"
    )
    week10.add_argument("rest", nargs=argparse.REMAINDER,
                        help="arguments for experiments/week10_benchmark.py")
    week10.set_defaults(func=command_week10)

    # week11 (observability: the drill, the artefacts, the controlled incidents, the suite)
    week11 = subparsers.add_parser(
        "week11",
        help="Week 11: drill | report | plant | eval (see week11/README.md)"
    )
    w11_actions = week11.add_subparsers(dest="week11_action", required=True)
    w11_drill = w11_actions.add_parser("drill", help="find the incident behind a vague complaint, timed")
    w11_drill.add_argument("--complaint",
                           default="A customer said it gave a wrong answer about refunds yesterday afternoon.")
    w11_drill.add_argument("--as-of", default=None, help="the day the complaint is handled (YYYY-MM-DD; default today)")
    w11_drill.add_argument("--procedure", choices=["slices", "indexed"], default="slices")
    w11_actions.add_parser("report", help="write week11/drill.md, cost_by_stage.md, tenx.md, trace.json")
    w11_plant = w11_actions.add_parser("plant", help="run one controlled incident through the real drafter")
    w11_plant.add_argument("fixture", choices=["refund-window-inverted", "duplicate-charge-denied"])
    w11_plant.add_argument("--force", action="store_true", help="re-run even if already planted")
    w11_eval = w11_actions.add_parser("eval", help="the Week 6 suite (frozen 25 + R05 live), output saved under week11/test_results/")
    w11_eval.add_argument("--judge", choices=["none", "auto"], default="none")
    w11_eval.add_argument("--label", default=None, help="label for the saved record (e.g. RED, GREEN)")
    w11_eval.add_argument("--fresh", action="store_true",
                          help="the full suite with every reply drafted fresh (python main.py eval), saved the same way")
    week11.set_defaults(func=command_week11)

    # capstone (Week 12: the whole application from one command)
    capstone = subparsers.add_parser(
        "capstone",
        help="Week 12: up | seed | mcp | build-ui | eval | trajectory | taxonomy | reopen | cost | sample | report"
    )
    cap_actions = capstone.add_subparsers(dest="capstone_action", required=True)
    cap_up = cap_actions.add_parser("up", help="start everything: MCP server, backend, dashboard at /app/")
    cap_up.add_argument("--port", type=int, default=8000)
    cap_up.add_argument("--no-browser", action="store_true")
    cap_up.add_argument("--no-mcp", action="store_true", help="do not start the HTTP MCP server (stdio fallback)")
    cap_up.add_argument("--no-ui", action="store_true", help="skip the frontend build check")
    cap_up.add_argument("--warm", action="store_true", help="load the local models at startup")
    cap_up.add_argument("--reseed", action="store_true", help="rebuild the demo data first")
    cap_seed = cap_actions.add_parser("seed", help="(re)build data/capstone/seed/*.json")
    cap_seed.set_defaults(reseed=True)
    cap_mcp = cap_actions.add_parser("mcp", help="run the help-centre MCP server alone over HTTP")
    cap_mcp.add_argument("--port", type=int, default=None)
    cap_actions.add_parser("build-ui", help="build the React dashboard into frontend/dist")
    for name, help_text in (("eval", "integrated Week 6 suite: run | judge | report"),
                            ("trajectory", "Week 8 trajectory eval on integrated cases"),
                            ("taxonomy", "re-score the Week 5 error taxonomy from failed traces"),
                            ("reopen", "simulated second-contact evaluation"),
                            ("cost", "cost per deflected ticket and the 10x plan"),
                            ("sample", "draw the uncurated random sample of tickets"),
                            ("report", "write every reports/*.md from the week12/*.json results")):
        sub = cap_actions.add_parser(name, help=help_text)
        sub.add_argument("rest", nargs=argparse.REMAINDER)
    capstone.set_defaults(func=command_capstone, reseed=False, no_ui=False, no_mcp=False, no_browser=False,
                          warm=False, port=8000, rest=[])

    # demo
    demo = subparsers.add_parser(
        "demo",
        help="run the full deliverable demo"
    )
    demo.set_defaults(func=command_demo)

    return parser


if __name__ == "__main__":
    _parser = build_parser()
    parsed, _extra = _parser.parse_known_args()
    if _extra and parsed.command == "capstone":
        # `python main.py capstone eval run --label x`: everything after the action belongs to
        # experiments/week12_eval.py, in the order it was typed.
        _idx = sys.argv.index(parsed.capstone_action)
        parsed.rest = sys.argv[_idx + 1:]
    elif _extra:
        _parser.error(f"unrecognized arguments: {' '.join(_extra)}")
    sys.exit(parsed.func(parsed) or 0)

