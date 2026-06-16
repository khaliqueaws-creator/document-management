import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documents.embeddings import EmbeddingError, get_titan_embedding
from documents.models import Document, DocumentChunk
from documents.opensearch_indexing import (
    get_chunk_index_name,
    get_document_index_name,
    get_opensearch_client,
)


class Command(BaseCommand):
    help = "Report AI Search health across PostgreSQL, Bedrock, and OpenSearch."

    def add_arguments(self, parser):
        parser.add_argument(
            "--query-text",
            default="employee onboarding policy",
            help="Sample text used when checking Bedrock embeddings.",
        )
        parser.add_argument(
            "--skip-bedrock",
            action="store_true",
            help="Skip the live Bedrock embedding call.",
        )

    def handle(self, *args, **options):
        self.errors = 0
        self.warnings = 0

        self.check_settings()
        self.check_postgres()
        self.check_opensearch()

        if options["skip_bedrock"]:
            self.report("bedrock", "warn", "skipped live embedding check")
        else:
            self.check_bedrock(options["query_text"])

        self.stdout.write(
            f"summary=done errors={self.errors} warnings={self.warnings}"
        )

        if self.errors:
            raise CommandError(
                "AI Search health checks failed: "
                f"errors={self.errors} warnings={self.warnings}"
            )

    def report(self, name, status, detail):
        if status == "error":
            self.errors += 1
        elif status == "warn":
            self.warnings += 1

        self.stdout.write(f"{name}={status} {detail}")

    def check_settings(self):
        self.report(
            "settings",
            "ok",
            " ".join([
                f"aws_region={settings.AWS_REGION}",
                f"embed_model={settings.BEDROCK_EMBED_MODEL_ID}",
                f"embedding_dimensions={settings.AI_EMBEDDING_DIMENSIONS}",
                f"opensearch_url={settings.OPENSEARCH_URL}",
            ]),
        )
        self.report(
            "aws_credentials",
            "ok" if os.environ.get("AWS_ACCESS_KEY_ID") else "warn",
            "access_key_configured="
            f"{bool(os.environ.get('AWS_ACCESS_KEY_ID'))} "
            "secret_key_configured="
            f"{bool(os.environ.get('AWS_SECRET_ACCESS_KEY'))}",
        )

    def check_postgres(self):
        try:
            document_count = Document.objects.count()
            chunk_count = DocumentChunk.objects.count()
            chunks_with_embeddings = (
                DocumentChunk.objects
                .exclude(embedding=[])
                .filter(embedding_model=settings.BEDROCK_EMBED_MODEL_ID)
            )
            embedding_count = chunks_with_embeddings.count()
            invalid_dimensions = sum(
                1
                for chunk in chunks_with_embeddings.iterator()
                if len(chunk.embedding or []) != settings.AI_EMBEDDING_DIMENSIONS
            )
        except Exception as error:
            self.report("postgres", "error", f"query_failed={error}")
            return

        self.report(
            "postgres",
            "ok",
            f"documents={document_count} chunks={chunk_count}",
        )

        if embedding_count == 0:
            self.report(
                "embeddings",
                "warn",
                "chunks_with_embeddings=0 run rebuild_embeddings or upload "
                "documents after Bedrock is configured",
            )
        elif invalid_dimensions:
            self.report(
                "embeddings",
                "error",
                f"chunks_with_embeddings={embedding_count} "
                f"invalid_dimensions={invalid_dimensions}",
            )
        else:
            self.report(
                "embeddings",
                "ok",
                f"chunks_with_embeddings={embedding_count} "
                f"dimensions={settings.AI_EMBEDDING_DIMENSIONS}",
            )

    def check_opensearch(self):
        try:
            client = get_opensearch_client()
            info = client.info()
            document_index = get_document_index_name()
            chunk_index = get_chunk_index_name()
            document_index_exists = client.indices.exists(index=document_index)
            chunk_index_exists = client.indices.exists(index=chunk_index)
        except Exception as error:
            self.report("opensearch", "error", f"connection_failed={error}")
            return

        version = info.get("version", {}).get("number", "unknown")
        self.report("opensearch", "ok", f"version={version}")

        self.report_index_count(
            client,
            "opensearch_documents",
            document_index,
            document_index_exists,
        )
        self.report_index_count(
            client,
            "opensearch_chunks",
            chunk_index,
            chunk_index_exists,
        )

    def report_index_count(self, client, name, index_name, exists):
        if not exists:
            self.report(
                name,
                "warn",
                f"index={index_name} exists=False run reindex_opensearch "
                "--create-indexes",
            )
            return

        try:
            count = client.count(index=index_name)["count"]
        except Exception as error:
            self.report(name, "error", f"index={index_name} count_failed={error}")
            return

        self.report(name, "ok", f"index={index_name} count={count}")

    def check_bedrock(self, query_text):
        try:
            embedding = get_titan_embedding(query_text)
        except EmbeddingError as error:
            self.report("bedrock", "error", f"embedding_failed={error}")
            return

        if len(embedding) != settings.AI_EMBEDDING_DIMENSIONS:
            self.report(
                "bedrock",
                "error",
                "embedding_dimensions="
                f"{len(embedding)} expected={settings.AI_EMBEDDING_DIMENSIONS}",
            )
            return

        self.report(
            "bedrock",
            "ok",
            f"embedding_dimensions={len(embedding)}",
        )
