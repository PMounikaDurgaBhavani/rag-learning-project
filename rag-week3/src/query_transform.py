"""Query transformations and diversity algorithms: Query Rewriting, HyDE, and MMR.

Enhances retrieval quality before and after search.
"""

import numpy as np
from typing import List, Dict, Any, Optional
from embeddings import get_model, embed_texts
from retriever import retrieve_chunks
from generator import get_pipeline, extract_text


def rewrite_query(raw_query: str) -> str:
    """Rewrite a messy, conversational, or vague user question into a crisp search query."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert search query optimizer for a technical help center.\n"
                "Rewrite the user's input into a concise, keyword-rich search query.\n"
                "Remove filler words, conversational phrases, and punctuation.\n"
                "Output ONLY the rewritten search query and nothing else."
            ),
        },
        {"role": "user", "content": f"User question: {raw_query}"},
    ]

    rewritten = extract_text(
        get_pipeline()(
            messages,
            max_new_tokens=40,
            do_sample=False,
            return_full_text=False
        )
    ).strip()

    # Clean up formatting artifacts
    if rewritten.lower().startswith("rewritten search query:"):
        rewritten = rewritten.split(":", 1)[-1].strip()
    if rewritten.startswith('"') and rewritten.endswith('"'):
        rewritten = rewritten[1:-1].strip()

    return rewritten or raw_query


def generate_hypothetical_document(query: str) -> str:
    """Generate a hypothetical help center document answering the query (HyDE)."""
    messages = [
        {
            "role": "system",
            "content": (
                "You are a technical writer for CloudDesk help center.\n"
                "Write a short, hypothetical documentation paragraph that directly answers the user's question.\n"
                "Include realistic technical terms, settings, and step-by-step instructions.\n"
                "Answer in under 60 words."
            ),
        },
        {"role": "user", "content": f"Question: {query}"},
    ]

    hypothetical_doc = extract_text(
        get_pipeline()(
            messages,
            max_new_tokens=80,
            do_sample=False,
            return_full_text=False
        )
    ).strip()

    return hypothetical_doc or query


def maximal_marginal_relevance(
    query_embedding: np.ndarray,
    candidate_embeddings: np.ndarray,
    candidates: List[Dict[str, Any]],
    top_k: int = 5,
    lambda_param: float = 0.6
) -> List[Dict[str, Any]]:
    """Compute Maximal Marginal Relevance (MMR) to balance relevance and diversity.
    
    Formula:
      MMR = argmax_{d in R \ S} [ lambda * Sim(d, q) - (1 - lambda) * max_{s in S} Sim(d, s) ]
    """
    if len(candidates) <= top_k:
        return candidates

    # Normalize vectors for cosine similarity
    def normalize(vecs):
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1e-10
        return vecs / norms

    q_norm = normalize(query_embedding.reshape(1, -1))[0]
    cand_norm = normalize(candidate_embeddings)

    # Initial query similarity
    sim_to_query = np.dot(cand_norm, q_norm)

    selected_indices: List[int] = []
    unselected_indices = list(range(len(candidates)))

    # First item is the one most similar to query
    first_idx = int(np.argmax(sim_to_query))
    selected_indices.append(first_idx)
    unselected_indices.remove(first_idx)

    # Greedily select remaining items using MMR
    while len(selected_indices) < top_k and unselected_indices:
        best_score = -float("inf")
        best_idx = -1

        for idx in unselected_indices:
            # Relevance to query
            relevance = sim_to_query[idx]
            
            # Maximum redundancy with already selected chunks
            redundancy = max(
                np.dot(cand_norm[idx], cand_norm[sel_idx])
                for sel_idx in selected_indices
            )

            mmr_score = (lambda_param * relevance) - ((1.0 - lambda_param) * redundancy)
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx

        if best_idx != -1:
            selected_indices.append(best_idx)
            unselected_indices.remove(best_idx)
        else:
            break

    mmr_results = []
    for rank, idx in enumerate(selected_indices, start=1):
        item = candidates[idx].copy()
        item["rank"] = rank
        mmr_results.append(item)

    return mmr_results


if __name__ == "__main__":
    messy_q = "hey uh can you tell me why the upload button isn't clicking or doing anything on my screen??"
    print(f"Original:  {messy_q}")
    print(f"Rewritten: {rewrite_query(messy_q)}")
    print(f"\nHyDE Document:\n{generate_hypothetical_document(messy_q)}")
