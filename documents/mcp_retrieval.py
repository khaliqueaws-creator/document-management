import logging
import time

from django.conf import settings
from django.urls import reverse

from .embeddings import EmbeddingError
from .models import Document
from .rag import RAGError, retrieve_question_context


logger = logging.getLogger(__name__)

ERROR_MESSAGES = {
    "invalid_request": "The retrieval request is invalid.",
    "embedding_provider_error": "The embedding provider is temporarily unavailable.",
    "retrieval_timeout": "Document retrieval timed out.",
    "retrieval_unavailable": "Document retrieval is temporarily unavailable.",
    "permission_context_missing": "Permission context is required for retrieval.",
    "permission_denied": "The user is not allowed to retrieve documents.",
    "internal_error": "Document retrieval failed unexpectedly.",
}


RETRYABLE_ERRORS = {
    "embedding_provider_error",
    "retrieval_timeout",
    "retrieval_unavailable",
    "internal_error",
}

PERMISSION_DENIED = object()


def build_error_response(code, request_id="", warnings=None):
    return {
        "status": "error",
        "error": {
            "code": code,
            "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES["internal_error"]),
            "retryable": code in RETRYABLE_ERRORS,
        },
        "results": [],
        "warnings": warnings or [],
        "trace": build_trace(request_id),
    }


def build_trace(request_id=""):
    trace = {}
    if request_id:
        trace["request_id"] = request_id
    return trace


def elapsed_ms(start_time):
    return int((time.perf_counter() - start_time) * 1000)


def log_retrieval_event(status, code, request_id, duration_ms, **fields):
    logger.info(
        "mcp_retrieval status=%s code=%s request_id=%s duration_ms=%s %s",
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


def get_request_id(payload):
    trace = payload.get("trace") or {}
    return trace.get("request_id", "")


def normalize_max_results(options):
    max_results = options.get("max_results") or settings.AI_RAG_TOP_K
    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        return settings.AI_RAG_TOP_K

    return max(1, max_results)


def normalize_max_chunk_chars(options):
    max_chars = options.get("max_chunk_chars") or settings.AI_RAG_MAX_CONTEXT_CHARS
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        return settings.AI_RAG_MAX_CONTEXT_CHARS

    return max(0, max_chars)


def get_accessible_documents_from_context(user_context):
    roles = set(user_context.get("roles") or [])
    groups = set(user_context.get("groups") or [])
    allowed_groups = {
        settings.OKTA_GROUP_VIEWER,
        settings.OKTA_GROUP_LOADER,
        settings.OKTA_GROUP_ADMIN,
    }

    if roles or groups:
        if roles.intersection({"viewer", "loader", "admin"}) or groups.intersection(
            allowed_groups
        ):
            return Document.objects.all()
        return PERMISSION_DENIED

    return None


def serialize_tags(value):
    if not value:
        return []

    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]

    return [
        item.strip()
        for item in str(value).split(",")
        if item.strip()
    ]


def serialize_uploaded_at(uploaded_at):
    if not uploaded_at:
        return None

    if hasattr(uploaded_at, "isoformat"):
        return uploaded_at.isoformat()

    return str(uploaded_at)


def serialize_context(context, max_chunk_chars):
    document = context["document"]
    snippet = (context.get("chunk_text") or "").strip()
    if max_chunk_chars:
        snippet = snippet[:max_chunk_chars]

    return {
        "citation_id": context["citation_id"],
        "document_id": context["document_id"],
        "chunk_id": context.get("chunk_id"),
        "chunk_index": context.get("chunk_index"),
        "score": context.get("score", 0),
        "snippet": snippet,
        "source": {
            "document_id": context["document_id"],
            "file_name": document.file.name if document.file else "",
            "document_type": document.document_type,
            "document_subtype": document.document_subtype,
            "department": document.department,
            "author": document.author,
            "tags": serialize_tags(document.tags),
            "uploaded_at": serialize_uploaded_at(document.uploaded_at),
            "open_url": reverse("secure_document_view", args=[context["document_id"]]),
        },
        "retrieval": {
            "index": settings.OPENSEARCH_CHUNK_INDEX,
            "embedding_model": settings.BEDROCK_EMBED_MODEL_ID,
            "retrieval_mode": "vector",
            "hydrated_from_postgres": True,
        },
    }


def build_ok_response(
    query,
    contexts,
    candidate_count,
    request_id="",
    empty_reason="",
    max_chunk_chars=None,
):
    max_chunk_chars = (
        settings.AI_RAG_MAX_CONTEXT_CHARS
        if max_chunk_chars is None
        else max_chunk_chars
    )
    results = [
        serialize_context(context, max_chunk_chars)
        for context in contexts
    ]

    return {
        "status": "ok",
        "query": query,
        "results": results,
        "summary": {
            "returned_count": len(results),
            "candidate_count": candidate_count,
            "trimmed_count": max(candidate_count - len(results), 0),
            "empty_reason": empty_reason,
        },
        "warnings": [],
        "trace": build_trace(request_id),
    }


def search_documents(payload, documents_queryset=None):
    start_time = time.perf_counter()
    payload = payload or {}
    request_id = get_request_id(payload)
    query = (payload.get("query") or "").strip()

    if not query:
        log_retrieval_event(
            "error",
            "invalid_request",
            request_id,
            elapsed_ms(start_time),
        )
        return build_error_response("invalid_request", request_id=request_id)

    user_context = payload.get("user_context") or {}
    accessible_documents = documents_queryset

    if accessible_documents is None:
        accessible_documents = get_accessible_documents_from_context(user_context)

    if accessible_documents is PERMISSION_DENIED:
        log_retrieval_event(
            "error",
            "permission_denied",
            request_id,
            elapsed_ms(start_time),
        )
        return build_error_response("permission_denied", request_id=request_id)

    if accessible_documents is None:
        log_retrieval_event(
            "error",
            "permission_context_missing",
            request_id,
            elapsed_ms(start_time),
        )
        return build_error_response(
            "permission_context_missing",
            request_id=request_id,
        )

    options = payload.get("options") or {}
    max_results = normalize_max_results(options)
    max_chunk_chars = normalize_max_chunk_chars(options)

    try:
        contexts = retrieve_question_context(
            query,
            top_k=max_results,
            documents_queryset=accessible_documents,
        )
    except EmbeddingError:
        log_retrieval_event(
            "error",
            "embedding_provider_error",
            request_id,
            elapsed_ms(start_time),
            max_results=max_results,
        )
        return build_error_response(
            "embedding_provider_error",
            request_id=request_id,
        )
    except RAGError as error:
        message = str(error).lower()
        code = "retrieval_unavailable"
        if "timeout" in message or "timed out" in message:
            code = "retrieval_timeout"
        log_retrieval_event(
            "error",
            code,
            request_id,
            elapsed_ms(start_time),
            max_results=max_results,
        )
        return build_error_response(code, request_id=request_id)
    except Exception:
        logger.exception(
            "mcp_retrieval status=error code=internal_error request_id=%s",
            request_id or "",
        )
        log_retrieval_event(
            "error",
            "internal_error",
            request_id,
            elapsed_ms(start_time),
            max_results=max_results,
        )
        return build_error_response("internal_error", request_id=request_id)

    if not contexts:
        log_retrieval_event(
            "ok",
            "empty",
            request_id,
            elapsed_ms(start_time),
            max_results=max_results,
            returned_count=0,
            empty_reason="no_accessible_context",
        )
        return build_ok_response(
            query,
            [],
            candidate_count=0,
            request_id=request_id,
            empty_reason="no_accessible_context",
            max_chunk_chars=max_chunk_chars,
        )

    log_retrieval_event(
        "ok",
        "success",
        request_id,
        elapsed_ms(start_time),
        max_results=max_results,
        returned_count=len(contexts),
    )
    return build_ok_response(
        query,
        contexts,
        candidate_count=len(contexts),
        request_id=request_id,
        max_chunk_chars=max_chunk_chars,
    )
