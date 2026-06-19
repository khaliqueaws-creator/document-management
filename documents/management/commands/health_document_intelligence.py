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
from documents.rag import RAGError, generate_answer_with_bedrock


class Command(BaseCommand):
    help = (
        "Report document intelligence health across PostgreSQL, OpenSearch, "
        "Bedrock, and MCP runtime settings."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--query-text",
            default="employee onboarding policy",
            help="Sample text used when checking Bedrock embeddings.",
        )
        parser.add_argument(
            "--answer-prompt",
            default="Reply with exactly this word: ok",
            help="Small prompt used when checking Bedrock answer generation.",
        )
        parser.add_argument(
            "--skip-live",
            action="store_true",
            help="Skip live Bedrock calls and only check settings, database, and OpenSearch.",
        )
        parser.add_argument(
            "--skip-bedrock-embedding",
            action="store_true",
            help="Skip the live Bedrock Titan embedding check.",
        )
        parser.add_argument(
            "--skip-bedrock-answer",
            action="store_true",
            help="Skip the live Bedrock Nova answer check.",
        )

    def handle(self, *args, **options):
        self.errors = 0
        self.warnings = 0

        self.check_runtime_settings()
        self.check_cost_controls()
        self.check_postgres()
        self.check_opensearch()

        skip_embedding = options["skip_live"] or options["skip_bedrock_embedding"]
        skip_answer = options["skip_live"] or options["skip_bedrock_answer"]

        if skip_embedding:
            self.report("bedrock_embedding", "warn", "skipped live embedding check")
        else:
            self.check_bedrock_embedding(options["query_text"])

        if skip_answer:
            self.report("bedrock_answer", "warn", "skipped live answer check")
        else:
            self.check_bedrock_answer(options["answer_prompt"])

        self.stdout.write(
            f"summary=done errors={self.errors} warnings={self.warnings}"
        )

        if self.errors:
            raise CommandError(
                "Document intelligence health checks failed: "
                f"errors={self.errors} warnings={self.warnings}"
            )

    def report(self, name, status, detail):
        if status == "error":
            self.errors += 1
        elif status == "warn":
            self.warnings += 1

        self.stdout.write(f"{name}={status} {detail}")

    def check_runtime_settings(self):
        self.report(
            "runtime",
            "ok",
            " ".join([
                f"aws_region={settings.AWS_REGION}",
                f"nova_model={settings.BEDROCK_NOVA_MODEL_ID}",
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
            f"{bool(os.environ.get('AWS_SECRET_ACCESS_KEY'))} "
            "session_token_configured="
            f"{bool(os.environ.get('AWS_SESSION_TOKEN'))}",
        )
        self.report(
            "mcp",
            "ok" if settings.MCP_RETRIEVAL_ENABLED and settings.MCP_INDEXING_ENABLED else "warn",
            " ".join([
                f"retrieval_enabled={settings.MCP_RETRIEVAL_ENABLED}",
                f"retrieval_fallback_enabled={settings.MCP_RETRIEVAL_FALLBACK_ENABLED}",
                f"indexing_enabled={settings.MCP_INDEXING_ENABLED}",
            ]),
        )

    def check_cost_controls(self):
        self.report(
            "limits",
            "ok",
            " ".join([
                f"bedrock_timeout_seconds={settings.BEDROCK_TIMEOUT_SECONDS}",
                f"opensearch_timeout_seconds={settings.OPENSEARCH_TIMEOUT_SECONDS}",
                f"embedding_max_chars={settings.AI_EMBEDDING_MAX_CHARS}",
                f"search_top_k={settings.AI_SEARCH_TOP_K}",
                f"rag_top_k={settings.AI_RAG_TOP_K}",
                f"rag_max_context_chars={settings.AI_RAG_MAX_CONTEXT_CHARS}",
                f"rag_max_answer_tokens={settings.AI_RAG_MAX_ANSWER_TOKENS}",
                f"rag_min_context_chars={settings.AI_RAG_MIN_CONTEXT_CHARS}",
                (
                    "rag_conversation_max_turns="
                    f"{settings.AI_RAG_CONVERSATION_MAX_TURNS}"
                ),
                (
                    "rag_conversation_max_chars="
                    f"{settings.AI_RAG_CONVERSATION_MAX_CHARS}"
                ),
            ]),
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

        if chunk_count and embedding_count == 0:
            self.report(
                "postgres_embeddings",
                "warn",
                "chunks_with_embeddings=0 run MCP indexing or rebuild_embeddings",
            )
        elif invalid_dimensions:
            self.report(
                "postgres_embeddings",
                "error",
                f"chunks_with_embeddings={embedding_count} "
                f"invalid_dimensions={invalid_dimensions}",
            )
        else:
            self.report(
                "postgres_embeddings",
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
                f"index={index_name} exists=False run reindex_opensearch --create-indexes",
            )
            return

        try:
            count = client.count(index=index_name)["count"]
        except Exception as error:
            self.report(name, "error", f"index={index_name} count_failed={error}")
            return

        self.report(name, "ok", f"index={index_name} count={count}")

    def check_bedrock_embedding(self, query_text):
        try:
            embedding = get_titan_embedding(query_text)
        except EmbeddingError as error:
            self.report("bedrock_embedding", "error", f"failed={error}")
            return

        if len(embedding) != settings.AI_EMBEDDING_DIMENSIONS:
            self.report(
                "bedrock_embedding",
                "error",
                f"dimensions={len(embedding)} expected={settings.AI_EMBEDDING_DIMENSIONS}",
            )
            return

        self.report("bedrock_embedding", "ok", f"dimensions={len(embedding)}")

    def check_bedrock_answer(self, answer_prompt):
        try:
            answer = generate_answer_with_bedrock(answer_prompt)
        except RAGError as error:
            self.report("bedrock_answer", "error", f"failed={error}")
            return

        self.report(
            "bedrock_answer",
            "ok" if answer.strip() else "error",
            f"non_empty={bool(answer.strip())} model={settings.BEDROCK_NOVA_MODEL_ID}",
        )
