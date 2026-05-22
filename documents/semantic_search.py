import numpy as np
from django.conf import settings

from .embeddings import get_titan_embedding
from .models import DocumentChunk


def cosine_similarity(vec1, vec2):
    try:
        left = np.array(vec1, dtype=float)
        right = np.array(vec2, dtype=float)
    except (TypeError, ValueError):
        return 0.0

    if left.ndim != 1 or right.ndim != 1:
        return 0.0

    if left.size == 0 or right.size == 0 or left.size != right.size:
        return 0.0

    left_norm = np.linalg.norm(left)
    right_norm = np.linalg.norm(right)

    if left_norm == 0 or right_norm == 0:
        return 0.0

    return float(np.dot(left, right) / (left_norm * right_norm))


def search_documents_by_meaning(query, top_k=None):
    top_k = top_k or settings.AI_SEARCH_TOP_K
    query = (query or "").strip()

    if not query:
        return []

    query_embedding = get_titan_embedding(query)
    best_by_document_id = {}
    chunks = (
        DocumentChunk.objects
        .select_related("document")
        .exclude(embedding=[])
    )

    for chunk in chunks:
        score = cosine_similarity(query_embedding, chunk.embedding)

        if score <= 0:
            continue

        current = best_by_document_id.get(chunk.document_id)
        if current is not None and current["score"] >= score:
            continue

        best_by_document_id[chunk.document_id] = {
            "document": chunk.document,
            "score": score,
            "best_chunk": chunk.chunk_text,
        }

    results = sorted(
        best_by_document_id.values(),
        key=lambda item: item["score"],
        reverse=True,
    )

    return results[:top_k]
