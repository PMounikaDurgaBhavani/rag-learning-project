import chromadb

from loader import load_articles
from chunking import STRATEGIES, create_chunks
from embeddings import create_embeddings


CHROMA_PATH = "./chroma_db"

# Default index used by the application.
DEFAULT_STRATEGY = "markdown"


def collection_name(strategy):
    return f"clouddesk_{strategy}"


def get_client(path=CHROMA_PATH):
    return chromadb.PersistentClient(path=path)


def clean_metadata(metadata):
    """Convert metadata into values ChromaDB accepts."""

    cleaned = {}

    for key, value in metadata.items():

        if hasattr(value, "isoformat"):
            cleaned[key] = value.isoformat()

        elif isinstance(value, (str, int, float, bool)):
            cleaned[key] = value

        else:
            cleaned[key] = str(value)

    return cleaned


def index_chunks(client, name, chunks, show_progress_bar=False):
    """Embed chunks and upsert them into a named collection."""

    # Rebuild from scratch so stale chunks never survive a re-ingest.
    try:
        client.delete_collection(name=name)
    except Exception:
        pass

    collection = client.get_or_create_collection(name=name)

    embeddings = create_embeddings(
        chunks,
        show_progress_bar=show_progress_bar
    )

    collection.upsert(
        ids=[
            chunk["metadata"]["chunk_id"]
            for chunk in chunks
        ],
        documents=[
            chunk["content"]
            for chunk in chunks
        ],
        embeddings=embeddings.tolist(),
        metadatas=[
            clean_metadata(chunk["metadata"])
            for chunk in chunks
        ]
    )

    return collection


def build_index(
    strategy,
    chunk_size=300,
    chunk_overlap=50,
    path=CHROMA_PATH,
    show_progress_bar=False
):
    """Build one persistent index for a single chunking strategy."""

    documents = load_articles()

    chunks = create_chunks(
        strategy,
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap
    )

    client = get_client(path)

    collection = index_chunks(
        client,
        collection_name(strategy),
        chunks,
        show_progress_bar=show_progress_bar
    )

    return collection


def build_all_indexes(
    chunk_size=300,
    chunk_overlap=50,
    path=CHROMA_PATH
):
    """Build one index per chunking strategy over the same documents."""

    results = []

    for strategy in STRATEGIES:

        collection = build_index(
            strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            path=path
        )

        results.append(
            {
                "strategy": strategy,
                "collection": collection_name(strategy),
                "chunks": collection.count()
            }
        )

        print(
            f"  {strategy:<12} -> "
            f"{collection_name(strategy):<22} "
            f"{collection.count()} chunks"
        )

    return results


# Backwards-compatible helper used by earlier scripts.
def create_vector_store():
    return build_index("recursive")


if __name__ == "__main__":

    print("=" * 70)
    print("BUILDING VECTOR INDEXES (one per chunking strategy)")
    print("=" * 70)

    build_all_indexes()

    print("\nDone.")
