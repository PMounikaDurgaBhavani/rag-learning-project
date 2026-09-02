"""Side-by-Side Retrieval Inspection View.

Provides an in-depth diagnostic interface showing queries, multi-stage retrieval
rankings (Dense vs BM25 vs Hybrid vs Reranked), grounding scores, and final answers.
"""

from typing import Optional, Dict, Any
from retriever import retrieve_chunks, build_where
from bm25_retriever import retrieve_bm25
from hybrid_retriever import retrieve_hybrid, reciprocal_rank_fusion
from reranker import rerank_chunks
from query_transform import rewrite_query, generate_hypothetical_document
from generator import generate_answer
from grounding import verify_answer
from vector_store import DEFAULT_STRATEGY


def inspect_query(
    query: str,
    top_k: int = 5,
    strategy: str = DEFAULT_STRATEGY,
    product_area: Optional[str] = None,
    article_id: Optional[str] = None,
    apply_rewrite: bool = False,
    apply_hyde: bool = False
) -> Dict[str, Any]:
    """Execute and display a comprehensive diagnostic inspection of query processing and retrieval."""
    where = build_where(product_area=product_area, article_id=article_id)

    print("\n" + "=" * 80)
    print("                      RETRIEVAL & GENERATION INSPECTION VIEW")
    print("=" * 80)

    # 1. Query Processing
    print(f"\n[1] INPUT QUERY: '{query}'")
    active_search_query = query

    if apply_rewrite:
        rewritten = rewrite_query(query)
        print(f"    ↳ REWRITTEN QUERY : '{rewritten}'")
        active_search_query = rewritten

    if apply_hyde:
        hyde_doc = generate_hypothetical_document(query)
        print(f"    ↳ HyDE HYPOTHESIS : '{hyde_doc[:120]}...'")
        active_search_query = hyde_doc

    if where:
        print(f"    ↳ METADATA FILTER : {where}")

    # 2. Multi-Stage Retrieval Comparison
    print("\n" + "-" * 80)
    print(f"[2] MULTI-STAGE RETRIEVAL COMPARISON (Top {top_k} Candidates)")
    print("-" * 80)

    dense_chunks = retrieve_chunks(active_search_query, top_k=top_k, strategy=strategy, where=where)
    bm25_chunks = retrieve_bm25(active_search_query, top_k=top_k, strategy=strategy, where=where)
    fused_chunks = reciprocal_rank_fusion(dense_chunks, bm25_chunks, k_constant=60)[:top_k]
    reranked_chunks = rerank_chunks(query, fused_chunks, top_k=top_k)

    # Print Side-by-side header
    print(f"{'Rank':<5} | {'DENSE (Semantic)':<22} | {'BM25 (Lexical)':<22} | {'HYBRID + RERANKED':<24}")
    print("-" * 80)

    for i in range(top_k):
        d_info = f"{dense_chunks[i]['chunk_id']} (d={dense_chunks[i]['distance']:.3f})" if i < len(dense_chunks) else "-"
        b_info = f"{bm25_chunks[i]['chunk_id']} (s={bm25_chunks[i]['bm25_score']:.2f})" if i < len(bm25_chunks) else "-"
        r_info = f"{reranked_chunks[i]['chunk_id']} (r={reranked_chunks[i].get('rerank_score', 0):.2f})" if i < len(reranked_chunks) else "-"
        
        print(f"#{i+1:<4} | {d_info:<22} | {b_info:<22} | {r_info:<24}")

    # 3. Context Inspection
    print("\n" + "-" * 80)
    print("[3] TOP RERANKED CHUNK PREVIEWS")
    print("-" * 80)
    for chunk in reranked_chunks[:3]:
        snippet = chunk["content"].replace("\n", " ")[:100]
        print(f"  * [{chunk['chunk_id']}] ({chunk['product_area']} - {chunk.get('section', 'General')})")
        print(f"    \"{snippet}...\"\n")

    # 4. End-to-End Generation & Grounding
    print("-" * 80)
    print("[4] GENERATION & GROUNDING VERIFICATION")
    print("-" * 80)
    
    gen_result = generate_answer(
        query=query,
        top_k=top_k,
        strategy=strategy,
        product_area=product_area,
        article_id=article_id
    )

    print(f"Answer: {gen_result['answer']}")
    if gen_result["refused"]:
        print(f"Refusal Reason: {gen_result['refusal_reason']}")
    else:
        print(f"\nVerified Citations ({len(gen_result['sources'])}):")
        for s in gen_result["sources"]:
            print(f"  {s['marker']} {s['chunk_id']} - Coverage: {s['coverage']*100:.1f}% | Section: {s['section']}")

    print("=" * 80 + "\n")
    return {
        "query": query,
        "dense": dense_chunks,
        "bm25": bm25_chunks,
        "reranked": reranked_chunks,
        "generation": gen_result
    }


if __name__ == "__main__":
    inspect_query("How do I update an expired card for CloudDesk payments in HC-003?")
