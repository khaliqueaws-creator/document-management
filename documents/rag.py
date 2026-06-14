import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from .embeddings import EmbeddingError, get_titan_embedding
from .models import Document
from .opensearch_indexing import get_chunk_index_name, get_opensearch_client


class RAGError(Exception):
    pass


LOW_CONTEXT_REFUSAL = (
    "The available documents do not contain enough relevant information to "
    "answer this question."
)


def get_accessible_documents(documents_queryset=None):
    return documents_queryset if documents_queryset is not None else Document.objects.all()


def retrieve_question_context(question, top_k=None, documents_queryset=None):
    top_k = top_k or settings.AI_RAG_TOP_K
    question = (question or "").strip()

    if not question:
        return []

    try:
        query_embedding = get_titan_embedding(question)
        client = get_opensearch_client()
        response = client.search(
            index=get_chunk_index_name(),
            body={
                "size": top_k * 3,
                "_source": [
                    "document_id",
                    "chunk_id",
                    "chunk_index",
                    "chunk_text",
                    "file_name",
                    "document_type",
                    "department",
                    "embedding_model",
                ],
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": query_embedding,
                            "k": top_k * 3,
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
    except EmbeddingError:
        raise
    except Exception as error:
        raise RAGError(f"OpenSearch retrieval failed: {error}") from error

    hits = response.get("hits", {}).get("hits", [])
    document_ids = [
        hit.get("_source", {}).get("document_id")
        for hit in hits
        if hit.get("_source", {}).get("document_id") is not None
    ]
    documents_by_id = get_accessible_documents(documents_queryset).in_bulk(
        document_ids
    )
    contexts = []
    seen_chunks = set()
    min_score = settings.AI_RAG_MIN_RETRIEVAL_SCORE

    for hit in hits:
        source = hit.get("_source", {})
        document_id = source.get("document_id")
        document = documents_by_id.get(document_id)
        score = float(hit.get("_score") or 0)

        if document is None:
            continue

        if min_score and score < min_score:
            continue

        chunk_key = (document_id, source.get("chunk_index"))
        if chunk_key in seen_chunks:
            continue
        seen_chunks.add(chunk_key)

        contexts.append({
            "citation_id": len(contexts) + 1,
            "document": document,
            "document_id": document_id,
            "chunk_id": source.get("chunk_id"),
            "chunk_index": source.get("chunk_index"),
            "chunk_text": source.get("chunk_text", ""),
            "score": score,
        })

        if len(contexts) == top_k:
            break

    return contexts


def has_sufficient_context(contexts):
    min_chars = settings.AI_RAG_MIN_CONTEXT_CHARS

    if min_chars <= 0:
        return bool(contexts)

    context_chars = sum(
        len((context.get("chunk_text") or "").strip())
        for context in contexts
    )
    return context_chars >= min_chars


def build_rag_prompt(question, contexts):
    context_blocks = []
    max_chars = settings.AI_RAG_MAX_CONTEXT_CHARS

    for context in contexts:
        document = context["document"]
        excerpt = (context["chunk_text"] or "").strip()[:max_chars]
        context_blocks.append(
            "\n".join([
                f"[{context['citation_id']}]",
                f"Document: {document.file.name}",
                f"Department: {document.department or 'Unknown'}",
                f"Type: {document.document_type or 'Unknown'}",
                f"Excerpt: {excerpt}",
            ])
        )

    return f"""
You are a document question-answering assistant.

Answer the user's question using only the provided document excerpts. Cite every
factual claim with bracketed citation ids like [1] or [2]. If the excerpts do
not contain enough information, say that the available documents do not contain
enough information to answer. Do not mention or infer from documents that are
not included in the excerpts.

Question:
{question}

Document excerpts:
{chr(10).join(context_blocks)}
""".strip()


def build_bedrock_rag_request(prompt):
    return {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"text": prompt},
                ],
            },
        ],
        "inferenceConfig": {
            "temperature": 0.2,
            "maxTokens": settings.AI_RAG_MAX_ANSWER_TOKENS,
        },
    }


def parse_bedrock_rag_response(payload):
    try:
        answer = payload["output"]["message"]["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as error:
        raise RAGError("Bedrock returned an invalid RAG response.") from error

    answer = (answer or "").strip()

    if not answer:
        raise RAGError("Bedrock returned an empty RAG answer.")

    return answer


def generate_answer_with_bedrock(prompt):
    try:
        client = boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
            config=Config(
                connect_timeout=settings.BEDROCK_TIMEOUT_SECONDS,
                read_timeout=settings.BEDROCK_TIMEOUT_SECONDS,
            ),
        )
        response = client.invoke_model(
            modelId=settings.BEDROCK_NOVA_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(build_bedrock_rag_request(prompt)),
        )
    except (BotoCoreError, ClientError) as error:
        raise RAGError(f"Unable to reach Bedrock: {error}") from error

    response_body = response.get("body")

    if response_body is None:
        raise RAGError("Bedrock returned an invalid response.")

    try:
        payload = json.loads(response_body.read())
    except (AttributeError, TypeError, ValueError) as error:
        raise RAGError("Bedrock returned a non-JSON API response.") from error

    return parse_bedrock_rag_response(payload)


def generate_rag_answer(prompt):
    return generate_answer_with_bedrock(prompt)


def answer_question(question, documents_queryset=None):
    question = (question or "").strip()

    if not question:
        return {
            "answer": "",
            "citations": [],
            "empty": True,
        }

    contexts = retrieve_question_context(
        question,
        documents_queryset=documents_queryset,
    )

    if not contexts:
        return {
            "answer": LOW_CONTEXT_REFUSAL,
            "citations": [],
            "empty": True,
        }

    if not has_sufficient_context(contexts):
        return {
            "answer": LOW_CONTEXT_REFUSAL,
            "citations": contexts,
            "empty": True,
        }

    prompt = build_rag_prompt(question, contexts)
    answer = generate_rag_answer(prompt)

    return {
        "answer": answer,
        "citations": contexts,
        "empty": False,
    }
