import numpy as np
from django.conf import settings

from .embeddings import EmbeddingError, get_titan_embedding
from .models import Document
from .opensearch_indexing import get_chunk_index_name, get_opensearch_client


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

    return search_documents_by_opensearch(query_embedding, top_k)


def search_documents_by_opensearch(query_embedding, top_k):
    try:
        client = get_opensearch_client()
        response = client.search(
            index=get_chunk_index_name(),
            body={
                "size": top_k * 20,
                "_source": [
                    "document_id",
                    "chunk_id",
                    "chunk_index",
                    "chunk_text",
                    "embedding_model",
                ],
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": query_embedding,
                            "k": top_k * 20,
                            "filter": {
                                "term": {
                                    "embedding_model": (
                                        settings.BEDROCK_EMBED_MODEL_ID
                                    )
                                }
                            },
                        }
                    }
                },
            },
        )
    except Exception as error:
        raise EmbeddingError(f"OpenSearch search failed: {error}") from error

    best_by_document_id = {}
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        document_id = source.get("document_id")

        if document_id is None:
            continue

        score = float(hit.get("_score") or 0)
        current = best_by_document_id.get(document_id)
        if current is not None and current["score"] >= score:
            continue

        best_by_document_id[document_id] = {
            "document_id": document_id,
            "score": score,
            "best_chunk": source.get("chunk_text", ""),
        }

    documents_by_id = Document.objects.in_bulk(best_by_document_id.keys())
    results = []

    for item in sorted(
        best_by_document_id.values(),
        key=lambda result: result["score"],
        reverse=True,
    ):
        document = documents_by_id.get(item["document_id"])
        if document is None:
            continue

        results.append({
            "document": document,
            "score": item["score"],
            "best_chunk": item["best_chunk"],
        })

        if len(results) == top_k:
            break

    return results
