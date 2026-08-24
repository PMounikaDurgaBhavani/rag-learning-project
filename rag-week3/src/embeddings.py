from sentence_transformers import SentenceTransformer

from loader import load_articles
from chunking import create_recursive_chunks


MODEL_NAME = "all-MiniLM-L6-v2"

# The model is expensive to load, so keep one instance per process.
_MODEL = None


def get_model():
    global _MODEL

    if _MODEL is None:
        _MODEL = SentenceTransformer(MODEL_NAME)

    return _MODEL


def embed_texts(texts, show_progress_bar=False):
    return get_model().encode(
        texts,
        show_progress_bar=show_progress_bar
    )


def embed_query(query):
    return get_model().encode([query]).tolist()


def create_embeddings(chunks, show_progress_bar=True):
    texts = [chunk["content"] for chunk in chunks]

    return embed_texts(
        texts,
        show_progress_bar=show_progress_bar
    )


if __name__ == "__main__":

    documents = load_articles()

    print(f"Articles loaded: {len(documents)}")

    chunks = create_recursive_chunks(
        documents,
        chunk_size=300,
        chunk_overlap=50
    )

    print(f"Chunks: {len(chunks)}")

    embeddings = create_embeddings(chunks)

    print(f"Embeddings generated: {len(embeddings)}")
    print(f"Embedding dimensions: {len(embeddings[0])}")

    print("\nFirst chunk:")
    print(chunks[0]["content"])

    print("\nFirst chunk ID:")
    print(chunks[0]["metadata"]["chunk_id"])

    print("\nFirst 5 embedding values:")
    print(embeddings[0][:5])
