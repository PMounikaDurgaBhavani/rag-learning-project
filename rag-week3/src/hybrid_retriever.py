"""Hybrid Retriever combining Dense Vector Search and Sparse BM25 via Reciprocal Rank Fusion (RRF).

Optionally applies a Cross-Encoder reranker as a second pass for state-of-the-art precision.
"""

from typing import List, Dict, Any, Optional
from retriever import retrieve_chunks, build_where
from bm25_retriever import retrieve_bm25
from reranker import rerank_chunks
from vector_store import DEFAULT_STRATEGY


def reciprocal_rank_fusion(
    dense_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    k_constant: int = 60
) -> List[Dict[str, Any]]:
    """Compute Reciprocal Rank Fusion (RRF) over dense and BM25 candidate lists.
    
    RRF Score formula:
      RRF_score(d) = sum_{m in {dense, bm25}} ( 1.0 / (k_constant + rank_m(d)) )
    """
    chunk_map: Dict[str, Dict[str, Any]] = {}
    dense_ranks: Dict[str, int] = {}
    bm25_ranks: Dict[str, int] = {}
    rrf_scores: Dict[str, float] = {}

    # Track dense rankings
    for chunk in dense_results:
        cid = chunk["chunk_id"]
        chunk_map[cid] = chunk
        dense_ranks[cid] = chunk["rank"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (k_constant + chunk["rank"]))

    # Track BM25 rankings
    for chunk in bm25_results:
        cid = chunk["chunk_id"]
        if cid not in chunk_map:
            chunk_map[cid] = chunk
        bm25_ranks[cid] = chunk["rank"]
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (k_constant + chunk["rank"]))

    # Build fused list
    fused = []
    for cid, score in rrf_scores.items():
        item = chunk_map[cid].copy()
        item["rrf_score"] = round(score, 6)
        item["dense_rank"] = dense_ranks.get(cid, None)
        item["bm25_rank"] = bm25_ranks.get(cid, None)
        fused.append(item)

    # Sort descending by RRF score
    fused.sort(key=lambda x: x["rrf_score"], reverse=True)

    for rank, item in enumerate(fused, start=1):
        item["rank"] = rank

    return fused


def retrieve_hybrid(
    query: str,
    top_k: int = 5,
    strategy: str = DEFAULT_STRATEGY,
    where: Optional[Dict[str, Any]] = None,
    candidate_k: Optional[int] = None,
    rerank: bool = False
) -> List[Dict[str, Any]]:
    """Retrieve chunks using hybrid dense + sparse search with optional cross-encoder reranking."""
    # Fetch more candidates for fusion and reranking
    candidates_count = candidate_k or max(top_k * 3, 15)

    dense_candidates = retrieve_chunks(
        query=query,
        top_k=candidates_count,
        strategy=strategy,
        where=where
    )

    bm25_candidates = retrieve_bm25(
        query=query,
        top_k=candidates_count,
        strategy=strategy,
        where=where
    )

    fused_candidates = reciprocal_rank_fusion(
        dense_results=dense_candidates,
        bm25_results=bm25_candidates,
        k_constant=60
    )

    if rerank:
        # Cross-encoder rerank the top fused candidates down to top_k
        return rerank_chunks(
            query=query,
            chunks=fused_candidates[:candidates_count],
            top_k=top_k
        )

    return fused_candidates[:top_k]


if __name__ == "__main__":
    test_query = "What happens when payment fails in HC-003?"
    print(f"HYBRID RETRIEVAL DEMO: '{test_query}'\n")

    print("--- 1. Dense Only (Top 3) ---")
    for r in retrieve_chunks(test_query, top_k=3):
        print(f"Rank {r['rank']}: [{r['chunk_id']}] dist={r['distance']:.4f}")

    print("\n--- 2. BM25 Only (Top 3) ---")
    for r in retrieve_bm25(test_query, top_k=3):
        print(f"Rank {r['rank']}: [{r['chunk_id']}] bm25={r['bm25_score']:.4f}")

    print("\n--- 3. Hybrid RRF (Top 3) ---")
    for r in retrieve_hybrid(test_query, top_k=3, rerank=False):
        print(f"Rank {r['rank']}: [{r['chunk_id']}] rrf={r['rrf_score']} (dense={r.get('dense_rank')}, bm25={r.get('bm25_rank')})")

    print("\n--- 4. Hybrid + Cross-Encoder Reranked (Top 3) ---")
    for r in retrieve_hybrid(test_query, top_k=3, rerank=True):
        print(f"Rank {r['rank']}: [{r['chunk_id']}] rerank={r.get('rerank_score'):.4f}")
