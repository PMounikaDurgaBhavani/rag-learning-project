"""Cross-Encoder reranker for second-pass precision scoring.

Computes joint cross-attention f(query, chunk) to re-rank candidate chunks.
"""

from typing import List, Dict, Any, Optional
from sentence_transformers import CrossEncoder

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RERANKER = None


def get_reranker(model_name: str = RERANKER_MODEL) -> CrossEncoder:
    global _RERANKER
    if _RERANKER is None:
        _RERANKER = CrossEncoder(model_name)
    return _RERANKER


def rerank_chunks(
    query: str,
    chunks: List[Dict[str, Any]],
    top_k: Optional[int] = None,
    model_name: str = RERANKER_MODEL
) -> List[Dict[str, Any]]:
    """Rerank candidate chunks using Cross-Encoder joint scoring."""
    if not chunks:
        return []

    reranker = get_reranker(model_name)
    pairs = [(query, chunk["content"]) for chunk in chunks]
    
    # Compute cross-encoder scores (higher = more relevant)
    scores = reranker.predict(pairs)

    reranked = []
    for chunk, score in zip(chunks, scores):
        chunk_copy = chunk.copy()
        chunk_copy["rerank_score"] = float(score)
        reranked.append(chunk_copy)

    # Sort descending by rerank score
    reranked.sort(key=lambda x: x["rerank_score"], reverse=True)

    # Re-assign ranks
    if top_k is not None:
        reranked = reranked[:top_k]

    for rank, item in enumerate(reranked, start=1):
        item["rank"] = rank

    return reranked


if __name__ == "__main__":
    test_query = "How long is a password reset link valid?"
    test_chunks = [
        {"chunk_id": "c1", "content": "Password reset links are valid for 24 hours.", "rank": 1},
        {"chunk_id": "c2", "content": "CloudDesk notifications can be set via email or SMS.", "rank": 2},
    ]
    results = rerank_chunks(test_query, test_chunks)
    for r in results:
        print(f"Rank {r['rank']}: [{r['chunk_id']}] score={r['rerank_score']:.4f}")
