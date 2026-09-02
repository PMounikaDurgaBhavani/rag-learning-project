"""BM25 lexical retriever for exact keyword matching (error codes, IDs, terms).

Uses rank_bm25 with custom tokenization and supports metadata filtering.
"""

import re
from typing import List, Dict, Any, Optional

from rank_bm25 import BM25Okapi
from loader import load_articles
from chunking import create_chunks
from vector_store import DEFAULT_STRATEGY


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\.\:]+")

def tokenize(text: str) -> List[str]:
    """Tokenize text preserving identifiers, error codes, and alphanumeric terms."""
    if not text:
        return []
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


class BM25Index:
    def __init__(self, chunks: List[Dict[str, Any]]):
        self.chunks = chunks
        self.tokenized_corpus = [
            tokenize(chunk["content"]) for chunk in chunks
        ]
        self.bm25 = BM25Okapi(self.tokenized_corpus)

    def search(
        self,
        query: str,
        top_k: int = 5,
        where: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Perform BM25 search with optional metadata filtering."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = self.bm25.get_scores(query_tokens)
        
        # Pair with chunks and filter
        scored_chunks = []
        for idx, score in enumerate(scores):
            chunk = self.chunks[idx]
            
            # Apply metadata filter if provided
            if where:
                if not self._matches_filter(chunk["metadata"], where):
                    continue
                    
            scored_chunks.append({
                "chunk_id": chunk["metadata"].get("chunk_id"),
                "article_id": chunk["metadata"].get("article_id"),
                "product_area": chunk["metadata"].get("product_area"),
                "last_updated": chunk["metadata"].get("last_updated"),
                "source_file": chunk["metadata"].get("source_file"),
                "section": chunk["metadata"].get("section"),
                "is_table": chunk["metadata"].get("is_table"),
                "content": chunk["content"],
                "bm25_score": float(score),
            })

        # Sort descending by score
        scored_chunks.sort(key=lambda x: x["bm25_score"], reverse=True)
        
        # Assign ranks
        results = scored_chunks[:top_k]
        for rank, item in enumerate(results, start=1):
            item["rank"] = rank

        return results

    def _matches_filter(self, metadata: Dict[str, Any], where: Dict[str, Any]) -> bool:
        """Check if metadata satisfies ChromaDB-style filter."""
        if "$and" in where:
            return all(self._matches_filter(metadata, cond) for cond in where["$and"])
        for k, v in where.items():
            if k == "$and":
                continue
            if metadata.get(k) != v:
                return False
        return True


# Global cache for BM25 indices per strategy
_BM25_INDICES: Dict[str, BM25Index] = {}

def clear_bm25_cache():
    """Clear cached BM25 indexes when new documents are ingested."""
    global _BM25_INDICES
    _BM25_INDICES.clear()

def get_bm25_index(
    strategy: str = DEFAULT_STRATEGY,
    chunk_size: int = 300,
    chunk_overlap: int = 50
) -> BM25Index:
    """Get or build cached BM25 index for the chosen chunking strategy."""
    key = f"{strategy}_{chunk_size}_{chunk_overlap}"
    if key not in _BM25_INDICES:
        documents = load_articles()
        chunks = create_chunks(
            strategy=strategy,
            documents=documents,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        _BM25_INDICES[key] = BM25Index(chunks)
    return _BM25_INDICES[key]


def retrieve_bm25(
    query: str,
    top_k: int = 5,
    strategy: str = DEFAULT_STRATEGY,
    where: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Retrieve top-K chunks using BM25 lexical keyword search."""
    index = get_bm25_index(strategy=strategy)
    return index.search(query=query, top_k=top_k, where=where)


if __name__ == "__main__":
    query = "HC-003 payment failed error 403"
    print(f"BM25 Search for: '{query}'")
    results = retrieve_bm25(query, top_k=3)
    for r in results:
        print(f"Rank {r['rank']}: [{r['chunk_id']}] score={r['bm25_score']:.4f} | {r['section']}")
