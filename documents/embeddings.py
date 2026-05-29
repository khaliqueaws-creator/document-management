import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from .models import DocumentChunk


class EmbeddingError(Exception):
    pass


def chunk_text(text, max_chars=None):
    max_chars = max_chars or settings.AI_EMBEDDING_MAX_CHARS
    text = (text or "").strip()

    if not text:
        return []

    chunks = []
    current = ""
    paragraphs = [part.strip() for part in text.split("\n\n")]

    for paragraph in paragraphs:
        if not paragraph:
            continue

        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""

            for start in range(0, len(paragraph), max_chars):
                chunk = paragraph[start:start + max_chars].strip()
                if chunk:
                    chunks.append(chunk)
            continue

        if not current:
            current = paragraph
            continue

        candidate = f"{current}\n\n{paragraph}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph

    if current:
        chunks.append(current)

    return chunks


def get_titan_embedding(text):
    text = (text or "").strip()

    if not text:
        raise EmbeddingError("No text is available for embedding.")

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
            modelId=settings.BEDROCK_EMBED_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({"inputText": text}),
        )
    except (BotoCoreError, ClientError) as error:
        raise EmbeddingError(f"Unable to reach Bedrock: {error}") from error

    response_body = response.get("body")

    if response_body is None:
        raise EmbeddingError("Bedrock returned an invalid embedding response.")

    try:
        payload = json.loads(response_body.read())
    except (AttributeError, TypeError, ValueError) as error:
        raise EmbeddingError(
            "Bedrock returned a non-JSON embedding response."
        ) from error

    embedding = payload.get("embedding")

    if not isinstance(embedding, list):
        raise EmbeddingError("Bedrock embedding response was invalid.")

    return embedding


def rebuild_document_embeddings(document):
    document.chunks.all().delete()

    text = (document.extracted_text or "").strip()

    if not text or text.startswith("TEXT_EXTRACTION_FAILED:"):
        return 0

    chunks = chunk_text(text)

    for index, chunk in enumerate(chunks):
        embedding = get_titan_embedding(chunk)

        if len(embedding) != settings.AI_EMBEDDING_DIMENSIONS:
            raise EmbeddingError(
                "Bedrock embedding dimension mismatch: "
                f"expected {settings.AI_EMBEDDING_DIMENSIONS}, "
                f"got {len(embedding)}."
            )

        DocumentChunk.objects.create(
            document=document,
            chunk_index=index,
            chunk_text=chunk,
            embedding=embedding,
            embedding_vector=embedding,
            embedding_model=settings.BEDROCK_EMBED_MODEL_ID,
        )

    return len(chunks)
