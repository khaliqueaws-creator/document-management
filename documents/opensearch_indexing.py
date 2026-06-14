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


def get_text_value(value):
    return value or ""


def get_document_mapping():
    return {
        "mappings": {
            "properties": {
                "document_id": {"type": "integer"},
                "file_name": {"type": "keyword"},
                "searchable_text": {"type": "text"},
                "extracted_text": {"type": "text"},
                "ocr_text": {"type": "text"},
                "document_type": {"type": "keyword"},
                "document_subtype": {"type": "keyword"},
                "department": {"type": "keyword"},
                "author": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "description": {"type": "text"},
                "tags": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
                "tags_list": {"type": "keyword"},
                "uploaded_at": {"type": "date"},
                "ai_document_type": {"type": "keyword"},
                "ai_department": {"type": "keyword"},
                "ai_tags": {"type": "text"},
                "ai_tags_list": {"type": "keyword"},
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
                "searchable_text": {"type": "text"},
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
                "tags_list": {"type": "keyword"},
                "uploaded_at": {"type": "date"},
                "ai_document_type": {"type": "keyword"},
                "ai_department": {"type": "keyword"},
                "ai_tags": {"type": "text"},
                "ai_tags_list": {"type": "keyword"},
                "ai_summary": {"type": "text"},
                "ai_metadata_provider": {"type": "keyword"},
                "ai_suggestion_status": {"type": "keyword"},
            }
        },
    }


def build_searchable_text(*values):
    return "\n".join(
        str(value).strip()
        for value in values
        if value and str(value).strip()
    )


def build_keyword_list(value):
    if not value:
        return []

    return [
        item.strip()
        for item in str(value).split(",")
        if item.strip()
    ]


def build_document_payload(document):
    extracted_text = get_text_value(document.extracted_text)
    ocr_text = get_text_value(document.ocr_text)

    return {
        "document_id": document.id,
        "file_name": document.file.name if document.file else "",
        "searchable_text": build_searchable_text(
            document.description,
            document.tags,
            document.ai_summary,
            document.ai_tags,
            extracted_text,
            ocr_text,
        ),
        "extracted_text": extracted_text,
        "ocr_text": ocr_text,
        "document_type": document.document_type,
        "document_subtype": document.document_subtype,
        "department": document.department,
        "author": document.author,
        "description": document.description,
        "tags": document.tags,
        "tags_list": build_keyword_list(document.tags),
        "uploaded_at": document.uploaded_at.isoformat()
        if document.uploaded_at else None,
        "ai_document_type": document.ai_document_type,
        "ai_department": document.ai_department,
        "ai_tags": document.ai_tags,
        "ai_tags_list": build_keyword_list(document.ai_tags),
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
        "searchable_text": build_searchable_text(
            chunk.chunk_text,
            document.description,
            document.tags,
            document.ai_summary,
            document.ai_tags,
        ),
        "embedding": chunk.embedding or [],
        "embedding_model": chunk.embedding_model,
        "file_name": document.file.name if document.file else "",
        "document_type": document.document_type,
        "document_subtype": document.document_subtype,
        "department": document.department,
        "author": document.author,
        "tags": document.tags,
        "tags_list": build_keyword_list(document.tags),
        "uploaded_at": document.uploaded_at.isoformat()
        if document.uploaded_at else None,
        "ai_document_type": document.ai_document_type,
        "ai_department": document.ai_department,
        "ai_tags": document.ai_tags,
        "ai_tags_list": build_keyword_list(document.ai_tags),
        "ai_summary": document.ai_summary,
        "ai_metadata_provider": document.ai_metadata_provider,
        "ai_suggestion_status": document.ai_suggestion_status,
    }


def build_metadata_filter_query(filters=None):
    filters = filters or {}
    clauses = []

    keyword_fields = {
        "document_type",
        "document_subtype",
        "department",
        "ai_document_type",
        "ai_department",
        "ai_metadata_provider",
        "ai_suggestion_status",
        "embedding_model",
    }
    text_keyword_fields = {
        "author": "author.raw",
        "tags": "tags_list",
        "ai_tags": "ai_tags_list",
    }

    for field in keyword_fields:
        value = filters.get(field)
        if value:
            clauses.append({"term": {field: value}})

    for field, keyword_field in text_keyword_fields.items():
        value = filters.get(field)
        if value:
            clauses.append({"term": {keyword_field: value}})

    uploaded_from = filters.get("uploaded_from")
    uploaded_to = filters.get("uploaded_to")
    if uploaded_from or uploaded_to:
        range_filter = {}
        if uploaded_from:
            range_filter["gte"] = uploaded_from
        if uploaded_to:
            range_filter["lte"] = uploaded_to
        clauses.append({"range": {"uploaded_at": range_filter}})

    if not clauses:
        return {"match_all": {}}

    return {"bool": {"filter": clauses}}


def ensure_indexes(client=None):
    client = client or get_opensearch_client()
    document_mapping = get_document_mapping()
    chunk_mapping = get_chunk_mapping()

    try:
        if not client.indices.exists(index=get_document_index_name()):
            client.indices.create(
                index=get_document_index_name(),
                body=document_mapping,
            )
        else:
            client.indices.put_mapping(
                index=get_document_index_name(),
                body=document_mapping["mappings"],
            )

        if not client.indices.exists(index=get_chunk_index_name()):
            client.indices.create(
                index=get_chunk_index_name(),
                body=chunk_mapping,
            )
        else:
            client.indices.put_mapping(
                index=get_chunk_index_name(),
                body=chunk_mapping["mappings"],
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
