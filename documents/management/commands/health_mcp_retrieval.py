import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documents.mcp_retrieval import search_documents


class Command(BaseCommand):
    help = "Report MCP retrieval health and run an optional live smoke test."

    def add_arguments(self, parser):
        parser.add_argument(
            "--query-text",
            default="employee onboarding policy",
            help="Sample query used for the live MCP retrieval check.",
        )
        parser.add_argument(
            "--max-results",
            type=int,
            default=3,
            help="Maximum results requested by the live MCP retrieval check.",
        )
        parser.add_argument(
            "--skip-live",
            action="store_true",
            help="Only report settings; do not call Bedrock or OpenSearch.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Print the raw MCP response for the live check.",
        )

    def handle(self, *args, **options):
        self.errors = 0
        self.warnings = 0

        self.check_settings()

        if options["skip_live"]:
            self.report("mcp_live", "warn", "skipped live retrieval check")
        else:
            self.check_live_retrieval(
                options["query_text"],
                options["max_results"],
                print_json=options["json"],
            )

        self.stdout.write(
            f"summary=done errors={self.errors} warnings={self.warnings}"
        )

        if self.errors:
            raise CommandError(
                "MCP retrieval health checks failed: "
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
            "mcp_settings",
            "ok" if settings.MCP_RETRIEVAL_ENABLED else "warn",
            " ".join([
                f"enabled={settings.MCP_RETRIEVAL_ENABLED}",
                f"fallback_enabled={settings.MCP_RETRIEVAL_FALLBACK_ENABLED}",
                f"rag_top_k={settings.AI_RAG_TOP_K}",
                f"max_context_chars={settings.AI_RAG_MAX_CONTEXT_CHARS}",
                f"embed_model={settings.BEDROCK_EMBED_MODEL_ID}",
                f"chunk_index={settings.OPENSEARCH_CHUNK_INDEX}",
            ]),
        )

    def check_live_retrieval(self, query_text, max_results, print_json=False):
        response = search_documents({
            "query": query_text,
            "user_context": {"roles": ["viewer"]},
            "options": {
                "max_results": max_results,
                "max_chunk_chars": settings.AI_RAG_MAX_CONTEXT_CHARS,
            },
            "trace": {
                "request_id": "health-mcp-retrieval",
                "source": "health_mcp_retrieval",
            },
        })

        if print_json:
            self.stdout.write(json.dumps(response, indent=2, default=str))

        status = response.get("status")
        if status == "ok":
            summary = response.get("summary") or {}
            returned_count = summary.get("returned_count", 0)
            empty_reason = summary.get("empty_reason", "")
            report_status = "ok" if returned_count else "warn"
            detail = (
                f"status=ok returned_count={returned_count} "
                f"candidate_count={summary.get('candidate_count', 0)}"
            )
            if empty_reason:
                detail = f"{detail} empty_reason={empty_reason}"
            self.report("mcp_live", report_status, detail)
            return

        error = response.get("error") or {}
        self.report(
            "mcp_live",
            "error",
            " ".join([
                f"status={status}",
                f"code={error.get('code', 'unknown')}",
                f"retryable={error.get('retryable', False)}",
            ]),
        )
