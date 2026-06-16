import logging
import time

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist

from .embeddings import EmbeddingError, chunk_text, rebuild_document_embeddings
from .models import Document
from .opensearch_indexing import (
    OpenSearchIndexingError,
    get_chunk_index_name,
    get_document_index_name,
    index_document as index_document_metadata,
    index_document_chunks,
    reindex_document,
)


logger = logging.getLogger(__name__)


ERROR_MESSAGES = {
    "invalid_request": "The indexing request is invalid.",
    "document_not_found": "The document could not be found.",
    "no_extracted_text": "The document has no extracted text to index.",
    "chunking_error": "The document text could not be chunked.",
    "embedding_provider_error": "The embedding provider is temporarily unavailable.",
    "indexing_unavailable": "Document indexing is temporarily unavailable.",
    "indexing_timeout": "Document indexing timed out.",
    "stale_content_version": "The indexing request is stale.",
    "permission_denied": "The caller is not allowed to index this document.",
    "internal_error": "Document indexing failed unexpectedly.",
}


RETRYABLE_ERRORS = {
    "embedding_provider_error",
    "indexing_unavailable",
    "indexing_timeout",
    "internal_error",
}


def build_trace(request_id=""):
    trace = {}
    if request_id:
        trace["request_id"] = request_id
    return trace


def elapsed_ms(start_time):
    return int((time.perf_counter() - start_time) * 1000)


def get_request_id(payload):
    trace = payload.get("trace") or {}
    return trace.get("request_id", "")


def get_document_payload(payload):
    document_payload = payload.get("document") or {}
    if not isinstance(document_payload, dict):
        return {}
    return document_payload


def get_options(payload):
    options = payload.get("options") or {}
    if not isinstance(options, dict):
        return {}
    return options


def get_document_id(payload):
    document_payload = get_document_payload(payload)
    document_id = document_payload.get("document_id", payload.get("document_id"))

    try:
        document_id = int(document_id)
    except (TypeError, ValueError):
        return None

    if document_id <= 0:
        return None

    return document_id


def get_content_version(payload):
    return get_document_payload(payload).get("content_version")


def option_bool(options, name, default):
    value = options.get(name, default)

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes")

    return bool(value)


def normalize_max_chunk_chars(options):
    max_chars = options.get("max_chunk_chars") or settings.AI_EMBEDDING_MAX_CHARS

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        return settings.AI_EMBEDDING_MAX_CHARS

    return max(1, max_chars)


def normalize_int(options, name, default, minimum=0):
    value = options.get(name, default)

    try:
        value = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, value)


def get_indexing_options(payload):
    options = get_options(payload)

    return {
        "chunking_strategy": options.get("chunking_strategy") or "paragraph",
        "chunking_version": options.get("chunking_version") or "paragraph-v1",
        "max_chunk_chars": normalize_max_chunk_chars(options),
        "chunk_overlap_chars": normalize_int(
            options,
            "chunk_overlap_chars",
            0,
            minimum=0,
        ),
        "embedding_model": (
            options.get("embedding_model") or settings.BEDROCK_EMBED_MODEL_ID
        ),
        "embedding_dimensions": normalize_int(
            options,
            "embedding_dimensions",
            settings.AI_EMBEDDING_DIMENSIONS,
            minimum=1,
        ),
        "index_document_metadata": option_bool(
            options,
            "index_document_metadata",
            True,
        ),
        "index_chunks": option_bool(options, "index_chunks", True),
        "replace_existing_chunks": option_bool(
            options,
            "replace_existing_chunks",
            True,
        ),
        "dry_run": option_bool(options, "dry_run", False),
    }


def validate_options(options):
    if options["chunking_strategy"] != "paragraph":
        return "invalid_request"

    if options["chunk_overlap_chars"] != 0:
        return "invalid_request"

    if options["embedding_model"] != settings.BEDROCK_EMBED_MODEL_ID:
        return "invalid_request"

    if options["embedding_dimensions"] != settings.AI_EMBEDDING_DIMENSIONS:
        return "invalid_request"

    return None


def build_summary(
    chunks_created=0,
    chunks_replaced=0,
    chunks_embedded=0,
    document_records_indexed=0,
    chunk_records_indexed=0,
    dry_run=False,
    idempotent_replay=False,
):
    return {
        "chunks_created": chunks_created,
        "chunks_replaced": chunks_replaced,
        "chunks_embedded": chunks_embedded,
        "document_records_indexed": document_records_indexed,
        "chunk_records_indexed": chunk_records_indexed,
        "dry_run": dry_run,
        "idempotent_replay": idempotent_replay,
    }


def build_indexing_metadata(options):
    return {
        "document_index": get_document_index_name(),
        "chunk_index": get_chunk_index_name(),
        "embedding_model": options["embedding_model"],
        "embedding_dimensions": options["embedding_dimensions"],
        "chunking_strategy": options["chunking_strategy"],
        "chunking_version": options["chunking_version"],
    }


def build_error_response(
    code,
    document_id=None,
    content_version=None,
    request_id="",
    warnings=None,
):
    response = {
        "status": "error",
        "document_id": document_id,
        "error": {
            "code": code,
            "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES["internal_error"]),
            "retryable": code in RETRYABLE_ERRORS,
        },
        "summary": build_summary(),
        "warnings": warnings or [],
        "trace": build_trace(request_id),
    }

    if content_version:
        response["content_version"] = content_version

    return response


def build_ok_response(
    document_id,
    options,
    summary,
    content_version=None,
    request_id="",
    warnings=None,
):
    response = {
        "status": "ok",
        "document_id": document_id,
        "summary": summary,
        "indexing": build_indexing_metadata(options),
        "warnings": warnings or [],
        "trace": build_trace(request_id),
    }

    if content_version:
        response["content_version"] = content_version

    return response


def log_indexing_event(status, code, request_id, duration_ms, **fields):
    logger.info(
        "mcp_indexing status=%s code=%s request_id=%s duration_ms=%s %s",
        status,
        code,
        request_id or "",
        duration_ms,
        " ".join(
            f"{key}={value}"
            for key, value in fields.items()
            if value is not None
        ),
    )


def has_usable_extracted_text(document):
    text = (document.extracted_text or "").strip()
    return bool(text) and not text.startswith("TEXT_EXTRACTION_FAILED:")


def dry_run_indexing(document, options):
    chunks = chunk_text(document.extracted_text, max_chars=options["max_chunk_chars"])
    existing_chunks = document.chunks.count()

    return build_summary(
        chunks_created=len(chunks),
        chunks_replaced=existing_chunks,
        chunks_embedded=0,
        document_records_indexed=0,
        chunk_records_indexed=0,
        dry_run=True,
    )


def write_indexes(document, options):
    if options["index_document_metadata"] and options["index_chunks"]:
        chunk_records_indexed = reindex_document(document, create_indexes=True)
        return 1, chunk_records_indexed

    if options["index_document_metadata"]:
        index_document_metadata(document)

    chunk_records_indexed = 0
    if options["index_chunks"]:
        chunk_records_indexed = index_document_chunks(document)

    document_records_indexed = 1 if options["index_document_metadata"] else 0
    return document_records_indexed, chunk_records_indexed


def index_document(payload):
    start_time = time.perf_counter()
    payload = payload or {}
    request_id = get_request_id(payload)
    document_id = get_document_id(payload)
    content_version = get_content_version(payload)
    options = get_indexing_options(payload)
    option_error = validate_options(options)

    if document_id is None or option_error:
        log_indexing_event(
            "error",
            "invalid_request",
            request_id,
            elapsed_ms(start_time),
        )
        return build_error_response(
            "invalid_request",
            document_id=document_id,
            content_version=content_version,
            request_id=request_id,
        )

    try:
        document = Document.objects.get(pk=document_id)
    except (Document.DoesNotExist, ObjectDoesNotExist):
        log_indexing_event(
            "error",
            "document_not_found",
            request_id,
            elapsed_ms(start_time),
            document_id=document_id,
        )
        return build_error_response(
            "document_not_found",
            document_id=document_id,
            content_version=content_version,
            request_id=request_id,
        )

    if options["replace_existing_chunks"] and not has_usable_extracted_text(document):
        log_indexing_event(
            "error",
            "no_extracted_text",
            request_id,
            elapsed_ms(start_time),
            document_id=document_id,
        )
        return build_error_response(
            "no_extracted_text",
            document_id=document_id,
            content_version=content_version,
            request_id=request_id,
        )

    try:
        if options["dry_run"]:
            summary = dry_run_indexing(document, options)
            log_indexing_event(
                "ok",
                "dry_run",
                request_id,
                elapsed_ms(start_time),
                document_id=document_id,
                chunks_created=summary["chunks_created"],
            )
            return build_ok_response(
                document_id,
                options,
                summary,
                content_version=content_version,
                request_id=request_id,
                warnings=["dry_run_no_writes"],
            )

        chunks_replaced = document.chunks.count()
        chunks_created = 0
        if options["replace_existing_chunks"]:
            chunks_created = rebuild_document_embeddings(document)
        else:
            chunks_created = document.chunks.count()

        document_records_indexed, chunk_records_indexed = write_indexes(
            document,
            options,
        )
    except EmbeddingError:
        log_indexing_event(
            "error",
            "embedding_provider_error",
            request_id,
            elapsed_ms(start_time),
            document_id=document_id,
        )
        return build_error_response(
            "embedding_provider_error",
            document_id=document_id,
            content_version=content_version,
            request_id=request_id,
        )
    except OpenSearchIndexingError as error:
        message = str(error).lower()
        code = "indexing_timeout" if "timeout" in message else "indexing_unavailable"
        log_indexing_event(
            "error",
            code,
            request_id,
            elapsed_ms(start_time),
            document_id=document_id,
        )
        return build_error_response(
            code,
            document_id=document_id,
            content_version=content_version,
            request_id=request_id,
        )
    except Exception:
        logger.exception(
            "mcp_indexing status=error code=internal_error request_id=%s "
            "document_id=%s",
            request_id or "",
            document_id,
        )
        return build_error_response(
            "internal_error",
            document_id=document_id,
            content_version=content_version,
            request_id=request_id,
        )

    summary = build_summary(
        chunks_created=chunks_created,
        chunks_replaced=chunks_replaced,
        chunks_embedded=chunks_created,
        document_records_indexed=document_records_indexed,
        chunk_records_indexed=chunk_records_indexed,
        dry_run=False,
        idempotent_replay=False,
    )
    log_indexing_event(
        "ok",
        "success",
        request_id,
        elapsed_ms(start_time),
        document_id=document_id,
        chunks_created=chunks_created,
        chunk_records_indexed=chunk_records_indexed,
    )
    return build_ok_response(
        document_id,
        options,
        summary,
        content_version=content_version,
        request_id=request_id,
    )
