import db
from embeddings import embed_query
from vector_store import DEFAULT_STRATEGY


def build_where(
    product_area=None,
    article_id=None,
    is_table=None
):
    """Build a metadata filter.

    The shape is inherited from the ChromaDB era and kept because the UI,
    the CLI and the experiments all pass it around; `_where_to_columns`
    flattens it into SQL column filters at the point of use.
    """

    conditions = []

    if product_area is not None:
        conditions.append({"product_area": product_area})

    if article_id is not None:
        conditions.append({"article_id": article_id})

    if is_table is not None:
        conditions.append({"is_table": is_table})

    if not conditions:
        return None

    if len(conditions) == 1:
        return conditions[0]

    return {"$and": conditions}


def _where_to_columns(where):
    """Flatten a Chroma filter into plain column equality filters."""

    if not where:
        return {}

    conditions = where.get("$and", [where])
    columns = {}
    for condition in conditions:
        for key, value in condition.items():
            if key in ("product_area", "article_id"):
                columns[key] = value
    return columns


def retrieve_chunks(
    query,
    top_k=5,
    strategy=DEFAULT_STRATEGY,
    where=None
):
    """Retrieve the top_k most similar chunks, optionally filtered."""

    return db.search(
        embed_query(query)[0],
        top_k=top_k,
        strategy=strategy,
        **_where_to_columns(where)
    )


def print_results(chunks, show_content=True):
    if not chunks:
        print("  (no chunks retrieved)")
        return

    for chunk in chunks:

        print(
            f"  {chunk['rank']}. "
            f"[{chunk['chunk_id']}] "
            f"{chunk['product_area']} | "
            f"distance={chunk['distance']:.4f}"
        )

        if show_content:
            preview = chunk["content"].replace("\n", " ")[:110]
            print(f"     {preview}...")


if __name__ == "__main__":

    query = "What should I do if my CloudDesk payment fails?"

    print("=" * 70)
    print(f"QUERY: {query}")
    print("=" * 70)

    print("\nUNFILTERED (top 5):")
    print_results(
        retrieve_chunks(query, top_k=5)
    )

    print("\nFILTERED to product_area='Billing' (top 5):")
    print_results(
        retrieve_chunks(
            query,
            top_k=5,
            where=build_where(product_area="Billing")
        )
    )

    print("\nFILTERED to product_area='Notifications' (top 5):")
    print("(wrong filter - shows filtering really changes results)")
    print_results(
        retrieve_chunks(
            query,
            top_k=5,
            where=build_where(product_area="Notifications")
        )
    )
