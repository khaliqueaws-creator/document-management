from django.conf import settings

from .models import Document, DocumentChunk


class OpenSearchIndexingError(Exception):
    pass


def get_opensearch_client():
    try:
        from opensearchpy import OpenSearch
    except ImportError as error:
        raise OpenSearchIndexingError(
            "opensearch-py is not installed."
        ) from error

    return OpenSearch(
        hosts=[settings.OPENSEARCH_URL],
        timeout=settings.OPENSEARCH_TIMEOUT_SECONDS,
    )


def get_document_index_name():
    return settings.OPENSEARCH_DOCUMENT_INDEX


def get_chunk_index_name():
    return settings.OPENSEARCH_CHUNK_INDEX


def get_document_mapping():
    return {
        "mappings": {
            "properties": {
                "document_id": {"type": "integer"},
                "file_name": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "document_subtype": {"type": "keyword"},
                "department": {"type": "keyword"},
                "author": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "description": {"type": "text"},
                "tags": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "uploaded_at": {"type": "date"},
                "ai_document_type": {"type": "keyword"},
                "ai_department": {"type": "keyword"},
                "ai_tags": {"type": "text"},
                "ai_summary": {"type": "text"},
                "ai_metadata_provider": {"type": "keyword"},
                "ai_suggestion_status": {"type": "keyword"},
            }
        }
    }


def get_chunk_mapping():
    return {
        "settings": {
            "index": {
                "knn": True,
            }
        },
        "mappings": {
            "properties": {
                "document_id": {"type": "integer"},
                "chunk_id": {"type": "integer"},
                "chunk_index": {"type": "integer"},
                "chunk_text": {"type": "text"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": settings.AI_EMBEDDING_DIMENSIONS,
                },
                "embedding_model": {"type": "keyword"},
                "file_name": {"type": "keyword"},
                "document_type": {"type": "keyword"},
                "document_subtype": {"type": "keyword"},
                "department": {"type": "keyword"},
                "author": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "tags": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "uploaded_at": {"type": "date"},
            }
        },
    }


def build_document_payload(document):
    return {
        "document_id": document.id,
        "file_name": document.file.name if document.file else "",
        "document_type": document.document_type,
        "document_subtype": document.document_subtype,
        "department": document.department,
        "author": document.author,
        "description": document.description,
        "tags": document.tags,
        "uploaded_at": document.uploaded_at.isoformat()
        if document.uploaded_at else None,
        "ai_document_type": document.ai_document_type,
        "ai_department": document.ai_department,
        "ai_tags": document.ai_tags,
        "ai_summary": document.ai_summary,
        "ai_metadata_provider": document.ai_metadata_provider,
        "ai_suggestion_status": document.ai_suggestion_status,
    }


def build_chunk_payload(chunk):
    document = chunk.document
    return {
        "document_id": document.id,
        "chunk_id": chunk.id,
        "chunk_index": chunk.chunk_index,
        "chunk_text": chunk.chunk_text,
        "embedding": chunk.embedding or [],
        "embedding_model": chunk.embedding_model,
        "file_name": document.file.name if document.file else "",
        "document_type": document.document_type,
        "document_subtype": document.document_subtype,
        "department": document.department,
        "author": document.author,
        "tags": document.tags,
        "uploaded_at": document.uploaded_at.isoformat()
        if document.uploaded_at else None,
    }


def ensure_indexes(client=None):
    client = client or get_opensearch_client()

    try:
        if not client.indices.exists(index=get_document_index_name()):
            client.indices.create(
                index=get_document_index_name(),
                body=get_document_mapping(),
            )

        if not client.indices.exists(index=get_chunk_index_name()):
            client.indices.create(
                index=get_chunk_index_name(),
                body=get_chunk_mapping(),
            )
    except Exception as error:
        raise OpenSearchIndexingError(
            f"Unable to ensure OpenSearch indexes: {error}"
        ) from error


def index_document(document, client=None):
    client = client or get_opensearch_client()

    try:
        client.index(
            index=get_document_index_name(),
            id=str(document.id),
            body=build_document_payload(document),
            refresh=False,
        )
    except Exception as error:
        raise OpenSearchIndexingError(
            f"Unable to index document {document.id}: {error}"
        ) from error


def delete_document(document_id, client=None):
    client = client or get_opensearch_client()

    try:
        client.delete(
            index=get_document_index_name(),
            id=str(document_id),
            ignore=[404],
            refresh=False,
        )
        client.delete_by_query(
            index=get_chunk_index_name(),
            body={"query": {"term": {"document_id": document_id}}},
            conflicts="proceed",
            refresh=False,
            ignore=[404],
        )
    except Exception as error:
        raise OpenSearchIndexingError(
            f"Unable to delete document {document_id} from OpenSearch: {error}"
        ) from error


def index_document_chunks(document, client=None):
    client = client or get_opensearch_client()
    chunks = (
        DocumentChunk.objects
        .select_related("document")
        .filter(document=document)
        .order_by("chunk_index")
    )
    actions = [
        {
            "_op_type": "index",
            "_index": get_chunk_index_name(),
            "_id": f"{document.id}:{chunk.chunk_index}",
            "_source": build_chunk_payload(chunk),
        }
        for chunk in chunks
        if chunk.embedding
    ]

    try:
        client.delete_by_query(
            index=get_chunk_index_name(),
            body={"query": {"term": {"document_id": document.id}}},
            conflicts="proceed",
            refresh=False,
            ignore=[404],
        )

        if actions:
            try:
                from opensearchpy.helpers import bulk
            except ImportError as error:
                raise OpenSearchIndexingError(
                    "opensearch-py is not installed."
                ) from error

            bulk(client, actions, refresh=False)
    except OpenSearchIndexingError:
        raise
    except Exception as error:
        raise OpenSearchIndexingError(
            f"Unable to index chunks for document {document.id}: {error}"
        ) from error

    return len(actions)


def reindex_document(document, client=None, create_indexes=False):
    client = client or get_opensearch_client()

    if create_indexes:
        ensure_indexes(client=client)

    index_document(document, client=client)
    return index_document_chunks(document, client=client)


def reindex_documents(queryset=None, create_indexes=False):
    client = get_opensearch_client()

    if create_indexes:
        ensure_indexes(client=client)

    queryset = queryset or Document.objects.all().order_by("id")
    processed = 0
    chunks_indexed = 0

    for document in queryset:
        processed += 1
        chunks_indexed += reindex_document(document, client=client)

    return {
        "processed": processed,
        "chunks_indexed": chunks_indexed,
    }
