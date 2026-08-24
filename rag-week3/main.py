"""CloudDesk help centre RAG application.

Usage
-----
    python main.py ingest
    python main.py ask "How long is a password reset link valid?"
    python main.py ask "How do I fix it?" --area Billing
    python main.py search "payment failed" --top-k 5
    python main.py demo
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
    get_client,
    collection_name,
)


# ============================================================
# Commands
# ============================================================

def command_ingest(args):

    print("=" * 72)
    print("INGESTING ARTICLES")
    print("=" * 72)

    print(
        f"chunk_size={args.chunk_size} "
        f"chunk_overlap={args.chunk_overlap}\n"
    )

    build_all_indexes(
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap
    )

    print(f"\nDefault index for queries: {DEFAULT_STRATEGY}")


def command_search(args):

    from retriever import retrieve_chunks, build_where, print_results

    where = build_where(
        product_area=args.area,
        article_id=args.article
    )

    print("=" * 72)
    print(f"SEARCH: {args.query}")

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


def command_demo(args):

    from generator import generate_answer, print_answer
    from retriever import retrieve_chunks, build_where, print_results

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
    print("# 3. METADATA FILTERING")
    print("#" * 72)

    ambiguous = "How do I fix it?"

    print(f"\nQuery: \"{ambiguous}\"")

    print("\nUnfiltered top 3:")
    print_results(
        retrieve_chunks(ambiguous, top_k=3),
        show_content=False
    )

    for area in ["Billing", "Notifications", "Storage"]:

        print(f"\nFiltered to product_area='{area}' top 3:")

        print_results(
            retrieve_chunks(
                ambiguous,
                top_k=3,
                where=build_where(product_area=area)
            ),
            show_content=False
        )


def command_status(args):

    client = get_client()

    print("=" * 72)
    print("INDEX STATUS")
    print("=" * 72)

    for strategy in STRATEGIES:

        name = collection_name(strategy)

        try:
            count = client.get_collection(name=name).count()
            marker = " (default)" if strategy == DEFAULT_STRATEGY else ""
            print(f"  {name:<24} {count:>5} chunks{marker}")

        except Exception:
            print(f"  {name:<24}     - not built (run: python main.py ingest)")


# ============================================================
# CLI
# ============================================================

def build_parser():

    parser = argparse.ArgumentParser(
        description="CloudDesk help centre RAG application"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ingest
    ingest = subparsers.add_parser(
        "ingest",
        help="chunk the articles and build one index per strategy"
    )
    ingest.add_argument("--chunk-size", type=int, default=300)
    ingest.add_argument("--chunk-overlap", type=int, default=50)
    ingest.set_defaults(func=command_ingest)

    # status
    status = subparsers.add_parser(
        "status",
        help="show which indexes exist"
    )
    status.set_defaults(func=command_status)

    # search
    search = subparsers.add_parser(
        "search",
        help="retrieve chunks without generating an answer"
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
