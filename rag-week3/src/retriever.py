from embeddings import embed_query
from vector_store import (
    get_client,
    collection_name,
    DEFAULT_STRATEGY,
)


def build_where(
    product_area=None,
    article_id=None,
    is_table=None
):
    """Build a ChromaDB metadata filter.

    ChromaDB requires an explicit $and when more than one field
    is constrained, so a single-key filter is passed through as-is.
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


def format_results(raw):
    """Flatten a ChromaDB query response into a list of chunk dicts."""

    if not raw["ids"] or not raw["ids"][0]:
        return []

    chunks = []

    for index in range(len(raw["ids"][0])):

        metadata = raw["metadatas"][0][index]

        chunks.append(
            {
                "rank": index + 1,
                "chunk_id": metadata.get("chunk_id"),
                "article_id": metadata.get("article_id"),
                "product_area": metadata.get("product_area"),
                "last_updated": metadata.get("last_updated"),
                "source_file": metadata.get("source_file"),
                "section": metadata.get("section"),
                "is_table": metadata.get("is_table"),
                "content": raw["documents"][0][index],
                "distance": raw["distances"][0][index],
            }
        )

    return chunks


def retrieve_chunks(
    query,
    top_k=5,
    strategy=DEFAULT_STRATEGY,
    where=None
):
    """Retrieve the top_k most similar chunks, optionally filtered."""

    client = get_client()

    collection = client.get_collection(
        name=collection_name(strategy)
    )

    query_kwargs = {
        "query_embeddings": embed_query(query),
        "n_results": top_k,
    }

    if where:
        query_kwargs["where"] = where

    return format_results(
        collection.query(**query_kwargs)
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
