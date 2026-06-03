import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documents.embeddings import EmbeddingError, get_titan_embedding
from documents.models import Document, DocumentChunk
from documents.opensearch_indexing import (
    get_chunk_index_name,
    get_opensearch_client,
)


class Command(BaseCommand):
    help = (
        "Validate Bedrock Titan embeddings and OpenSearch indexed vector "
        "records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--query-text",
            default="employee onboarding policy",
            help="Sample text used to invoke the Titan embedding model.",
        )
        parser.add_argument(
            "--sample-size",
            type=int,
            default=3,
            help="Number of OpenSearch chunk records to inspect.",
        )

    def handle(self, *args, **options):
        query_text = options["query_text"]
        sample_size = options["sample_size"]

        self.stdout.write(f"AWS_REGION={settings.AWS_REGION}")
        self.stdout.write(f"BEDROCK_EMBED_MODEL_ID={settings.BEDROCK_EMBED_MODEL_ID}")
        self.stdout.write(
            f"AI_EMBEDDING_DIMENSIONS={settings.AI_EMBEDDING_DIMENSIONS}"
        )
        self.stdout.write(
            f"AWS_ACCESS_KEY_ID configured={bool(os.environ.get('AWS_ACCESS_KEY_ID'))}"
        )
        self.stdout.write(
            "AWS_SECRET_ACCESS_KEY configured="
            f"{bool(os.environ.get('AWS_SECRET_ACCESS_KEY'))}"
        )

        try:
            embedding = get_titan_embedding(query_text)
        except EmbeddingError as error:
            raise CommandError(f"Bedrock embedding validation failed: {error}")

        if len(embedding) != settings.AI_EMBEDDING_DIMENSIONS:
            raise CommandError(
                "Bedrock embedding dimension mismatch: "
                f"expected {settings.AI_EMBEDDING_DIMENSIONS}, "
                f"got {len(embedding)}."
            )

        self.stdout.write(
            f"Bedrock sample embedding dimensions={len(embedding)}"
        )

        db_chunks = (
            DocumentChunk.objects
            .exclude(embedding=[])
            .filter(embedding_model=settings.BEDROCK_EMBED_MODEL_ID)
        )
        db_chunk_count = db_chunks.count()
        bad_db_dimensions = sum(
            1
            for chunk in db_chunks.iterator()
            if len(chunk.embedding or []) != settings.AI_EMBEDDING_DIMENSIONS
        )

        if bad_db_dimensions:
            raise CommandError(
                f"{bad_db_dimensions} PostgreSQL chunks have invalid "
                "embedding dimensions."
            )

        self.stdout.write(f"db_chunks_with_embeddings={db_chunk_count}")

        try:
            client = get_opensearch_client()
            chunk_index = get_chunk_index_name()
            os_chunk_count = client.count(index=chunk_index)["count"]
            response = client.search(
                index=chunk_index,
                body={
                    "size": sample_size,
                    "_source": [
                        "document_id",
                        "chunk_id",
                        "chunk_index",
                        "embedding",
                        "embedding_model",
                    ],
                    "query": {
                        "bool": {
                            "filter": [
                                {"exists": {"field": "embedding"}},
                                {
                                    "term": {
                                        "embedding_model": (
                                            settings.BEDROCK_EMBED_MODEL_ID
                                        )
                                    }
                                },
                            ]
                        }
                    },
                },
            )
        except Exception as error:
            raise CommandError(f"OpenSearch embedding validation failed: {error}")

        hits = response.get("hits", {}).get("hits", [])
        invalid_os_dimensions = 0
        missing_documents = 0

        for hit in hits:
            source = hit.get("_source", {})
            if len(source.get("embedding") or []) != settings.AI_EMBEDDING_DIMENSIONS:
                invalid_os_dimensions += 1

            document_id = source.get("document_id")
            if not Document.objects.filter(id=document_id).exists():
                missing_documents += 1

        if invalid_os_dimensions:
            raise CommandError(
                f"{invalid_os_dimensions} sampled OpenSearch chunks have "
                "invalid embedding dimensions."
            )

        if missing_documents:
            raise CommandError(
                f"{missing_documents} sampled OpenSearch chunks do not trace "
                "back to PostgreSQL Document records."
            )

        self.stdout.write(f"os_chunks={os_chunk_count}")
        self.stdout.write(f"os_sampled_chunks={len(hits)}")
        self.stdout.write(
            "Done. Bedrock and OpenSearch embedding validation succeeded."
        )
