import json
import logging
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from .ai_metadata import (
    MetadataSuggestionError,
    parse_metadata_json_response,
    suggest_metadata,
    suggest_metadata_with_bedrock,
    suggest_metadata_with_gemini,
    suggest_metadata_with_ollama,
    validate_explanation_evidence,
)
from .embeddings import (
    EmbeddingError,
    chunk_text,
    get_titan_embedding,
    rebuild_document_embeddings,
)
from .mcp_retrieval import search_documents as mcp_search_documents
from .mcp_indexing import index_document as mcp_index_document
from .metadata_quality import (
    MetadataQualityError,
    build_metadata_quality_prompt,
    parse_metadata_quality_response,
    quality_level_for_score,
    review_document_metadata,
)
from .models import Document, MetadataQualityReview
from .opensearch_indexing import (
    OpenSearchIndexingError,
    build_metadata_filter_query,
    build_chunk_payload,
    build_document_payload,
    delete_document as delete_indexed_document,
    ensure_indexes,
    get_chunk_mapping,
    get_document_mapping,
    index_document,
)
from .rag import (
    LOW_CONTEXT_REFUSAL,
    RAGError,
    answer_question,
    build_bedrock_rag_request,
    build_conversation_retrieval_query,
    build_rag_prompt,
    generate_rag_answer,
    has_sufficient_context,
    parse_bedrock_rag_response,
    retrieve_answer_context,
    retrieve_question_context,
)
from .semantic_search import cosine_similarity, search_documents_by_meaning
from .views import (
    hydrate_ask_conversation,
    invalidate_metadata_quality_review,
    metadata_quality_dashboard,
    run_selected_metadata_quality_reviews,
    run_metadata_quality_review,
    serialize_ask_turn,
    store_ai_metadata_suggestions,
    try_index_document_for_search,
    try_rebuild_document_embeddings,
    try_reindex_document,
)
from .views import accept_ai_metadata, reject_ai_metadata


class LoggingSettingsTests(SimpleTestCase):
    def test_mcp_loggers_emit_info_logs(self):
        self.assertEqual(
            logging.getLogger("documents.mcp_indexing").getEffectiveLevel(),
            logging.INFO,
        )
        self.assertEqual(
            logging.getLogger("documents.mcp_retrieval").getEffectiveLevel(),
            logging.INFO,
        )
        self.assertEqual(
            logging.getLogger("documents.rag").getEffectiveLevel(),
            logging.INFO,
        )


class MetadataSuggestionTests(SimpleTestCase):
    def test_parse_ollama_json_response_cleans_and_truncates_values(self):
        suggestions = parse_metadata_json_response(
            """
            {
                "document_type": " Invoice ",
                "department": " Finance ",
                "tags": " payment, vendor ",
                "summary": "Monthly vendor invoice."
            }
            """
        )

        self.assertEqual(suggestions["document_type"], "Invoice")
        self.assertEqual(suggestions["department"], "Finance")
        self.assertEqual(suggestions["tags"], "payment, vendor")
        self.assertEqual(suggestions["summary"], "Monthly vendor invoice.")

    def test_parse_ollama_json_response_rejects_invalid_json(self):
        with self.assertRaises(MetadataSuggestionError):
            parse_metadata_json_response("not json")

    def test_parse_metadata_json_response_accepts_wrapped_json(self):
        suggestions = parse_metadata_json_response(
            """
            ```json
            {
                "document_type": "Report",
                "department": "Operations",
                "tags": "report, operations",
                "summary": "Operations report."
            }
            ```
            """
        )

        self.assertEqual(suggestions["document_type"], "Report")
        self.assertEqual(suggestions["department"], "Operations")

    def test_parse_metadata_json_response_returns_explainability(self):
        suggestions = parse_metadata_json_response(
            json.dumps({
                "document_type": {
                    "value": "Policy",
                    "confidence": "HIGH",
                    "reason": "The document defines employee coverage rules.",
                    "evidence": "Employees are eligible for health coverage.",
                },
                "department": {
                    "value": "HR",
                    "confidence": "medium",
                    "reason": "The text discusses employee benefits.",
                    "evidence": "Eligible employees may enroll.",
                },
                "tags": {
                    "value": "benefits, health, enrollment",
                    "confidence": "medium",
                    "reason": "These topics appear throughout the document.",
                    "evidence": "Health coverage enrollment begins...",
                },
                "summary": {
                    "value": "A policy describing employee health coverage.",
                    "confidence": "low",
                    "reason": "The available excerpt is brief.",
                    "evidence": "",
                },
            })
        )

        self.assertEqual(suggestions["document_type"], "Policy")
        self.assertEqual(
            suggestions["explanation"]["document_type"]["confidence"],
            "high",
        )
        self.assertIn(
            "coverage rules",
            suggestions["explanation"]["document_type"]["reason"],
        )
        self.assertEqual(
            suggestions["explanation"]["department"]["evidence"],
            "Eligible employees may enroll.",
        )

    def test_parse_metadata_json_response_rejects_unknown_confidence(self):
        suggestions = parse_metadata_json_response(
            json.dumps({
                "document_type": {
                    "value": "Policy",
                    "confidence": "92 percent",
                    "reason": "Policy language is present.",
                    "evidence": "This policy applies...",
                },
            })
        )

        self.assertEqual(
            suggestions["explanation"]["document_type"]["confidence"],
            "",
        )

    def test_validate_explanation_evidence_downgrades_unmatched_excerpt(self):
        suggestions = {
            "explanation": {
                "document_type": {
                    "confidence": "high",
                    "reason": "The document defines a policy.",
                    "evidence": "This sentence was not in the document.",
                },
                "department": {
                    "confidence": "medium",
                    "reason": "The document discusses employee benefits.",
                    "evidence": "Eligible employees may enroll.",
                },
            },
        }

        validate_explanation_evidence(
            suggestions,
            "Eligible employees\nmay enroll.",
        )

        self.assertEqual(
            suggestions["explanation"]["document_type"]["confidence"],
            "low",
        )
        self.assertEqual(
            suggestions["explanation"]["document_type"]["evidence"],
            "",
        )
        self.assertEqual(
            suggestions["explanation"]["department"]["evidence"],
            "Eligible employees may enroll.",
        )

    def test_suggest_metadata_requires_extracted_text(self):
        with self.assertRaisesMessage(
            MetadataSuggestionError,
            "No extracted text is available",
        ):
            suggest_metadata_with_ollama("")

    @override_settings(
        AI_METADATA_MAX_CHARS=20,
        OLLAMA_BASE_URL="http://ollama:11434",
        OLLAMA_MODEL="qwen2.5:0.5b",
        OLLAMA_TIMEOUT_SECONDS=45,
        OLLAMA_NUM_CTX=1024,
    )
    @patch("documents.ai_metadata.requests.post")
    def test_suggest_metadata_calls_ollama_generate_api(self, mock_post):
        response = Mock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "response": (
                '{"document_type": "Policy", "department": "HR", '
                '"tags": "benefits, policy", "summary": "Benefits policy."}'
            )
        }
        mock_post.return_value = response

        suggestions = suggest_metadata_with_ollama("A benefits policy document")

        self.assertEqual(suggestions["document_type"], "Policy")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "http://ollama:11434/api/generate")
        self.assertEqual(kwargs["json"]["model"], "qwen2.5:0.5b")
        self.assertEqual(kwargs["json"]["format"], "json")
        self.assertFalse(kwargs["json"]["stream"])
        self.assertEqual(kwargs["json"]["options"]["num_ctx"], 1024)

    @override_settings(
        AI_METADATA_MAX_CHARS=20,
        GEMINI_BASE_URL="https://generativelanguage.googleapis.com",
        GEMINI_MODEL="gemini-2.5-flash",
        GEMINI_API_KEY="test-key",
        OLLAMA_TIMEOUT_SECONDS=45,
    )
    @patch("documents.ai_metadata.requests.post")
    def test_suggest_metadata_calls_gemini_generate_content_api(
        self,
        mock_post,
    ):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": (
                                    '{"document_type": "Invoice", '
                                    '"department": "Finance", '
                                    '"tags": "invoice, finance", '
                                    '"summary": "A finance invoice."}'
                                )
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = response

        suggestions = suggest_metadata_with_gemini("A vendor invoice")

        self.assertEqual(suggestions["department"], "Finance")
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0],
            (
                "https://generativelanguage.googleapis.com"
                "/v1beta/models/gemini-2.5-flash:generateContent"
            ),
        )
        self.assertEqual(kwargs["params"]["key"], "test-key")
        self.assertEqual(
            kwargs["json"]["generationConfig"]["responseMimeType"],
            "application/json",
        )

    @override_settings(
        AI_METADATA_MAX_CHARS=20,
        AWS_REGION="us-east-1",
        BEDROCK_NOVA_MODEL_ID="amazon.nova-lite-v1:0",
        BEDROCK_TIMEOUT_SECONDS=45,
    )
    @patch("documents.ai_metadata.boto3.client")
    def test_suggest_metadata_calls_bedrock_nova_lite(self, mock_client):
        client = Mock()
        client.invoke_model.return_value = {
            "body": BytesIO(
                json.dumps(
                    {
                        "output": {
                            "message": {
                                "content": [
                                    {
                                        "text": (
                                            '{"document_type": "Report", '
                                            '"department": "Operations", '
                                            '"tags": "report, operations", '
                                            '"summary": "Operations report."}'
                                        )
                                    }
                                ]
                            }
                        }
                    }
                ).encode("utf-8")
            )
        }
        mock_client.return_value = client

        suggestions = suggest_metadata_with_bedrock(
            "An operations report with extra text beyond the limit"
        )

        self.assertEqual(suggestions["document_type"], "Report")
        self.assertEqual(suggestions["department"], "Operations")
        mock_client.assert_called_once()
        client_args, client_kwargs = mock_client.call_args
        self.assertEqual(client_args[0], "bedrock-runtime")
        self.assertEqual(client_kwargs["region_name"], "us-east-1")
        client.invoke_model.assert_called_once()
        _, invoke_kwargs = client.invoke_model.call_args
        self.assertEqual(
            invoke_kwargs["modelId"],
            "amazon.nova-lite-v1:0",
        )
        self.assertEqual(invoke_kwargs["contentType"], "application/json")
        self.assertEqual(invoke_kwargs["accept"], "application/json")
        body = json.loads(invoke_kwargs["body"])
        prompt = body["messages"][0]["content"][0]["text"]
        self.assertIn("An operations report", prompt)
        self.assertNotIn("extra text beyond", prompt)
        self.assertIn('"confidence": "high|medium|low"', prompt)
        self.assertIn("short verbatim excerpt", prompt)

    @override_settings(AI_METADATA_PROVIDER="bedrock")
    @patch("documents.ai_metadata.suggest_metadata_with_bedrock")
    def test_suggest_metadata_dispatches_to_bedrock(self, mock_bedrock):
        mock_bedrock.return_value = {"document_type": "Report"}

        self.assertEqual(
            suggest_metadata("Some document text"),
            {"document_type": "Report"},
        )
        mock_bedrock.assert_called_once_with("Some document text")

    @override_settings(AI_METADATA_PROVIDER="unknown")
    def test_suggest_metadata_rejects_unknown_provider(self):
        with self.assertRaisesMessage(
            MetadataSuggestionError,
            "Unsupported AI metadata provider",
        ):
            suggest_metadata("Some document text")


class MetadataExplainabilityTests(SimpleTestCase):
    def test_document_explanation_items_are_ordered_for_review(self):
        document = Document(
            ai_document_type="Policy",
            ai_department="HR",
            ai_tags="benefits, health",
            ai_summary="Employee health policy.",
            ai_explanation={
                "document_type": {
                    "confidence": "high",
                    "reason": "The text explicitly identifies a policy.",
                    "evidence": "This employee health policy...",
                },
                "department": {
                    "confidence": "medium",
                    "reason": "The topic concerns employee benefits.",
                    "evidence": "Eligible employees...",
                },
            },
        )

        items = document.ai_explanation_items

        self.assertEqual(
            [item["key"] for item in items],
            ["document_type", "department", "tags", "summary"],
        )
        self.assertEqual(items[0]["confidence"], "high")
        self.assertEqual(items[0]["value"], "Policy")
        self.assertEqual(items[2]["confidence"], "")

    @override_settings(AI_METADATA_PROVIDER="bedrock")
    @patch("documents.views.timezone.now")
    @patch("documents.views.suggest_metadata")
    def test_store_ai_metadata_suggestions_persists_explanation(
        self,
        mock_suggest,
        mock_now,
    ):
        suggested_at = Mock()
        mock_now.return_value = suggested_at
        mock_suggest.return_value = {
            "document_type": "Policy",
            "department": "HR",
            "tags": "benefits, health",
            "summary": "Employee health policy.",
            "explanation": {
                "document_type": {
                    "confidence": "high",
                    "reason": "The document calls itself a policy.",
                    "evidence": "Employee Health Policy",
                },
            },
        }
        document = Mock(
            extracted_text="Employee Health Policy",
            ai_explanation={"stale": True},
        )

        store_ai_metadata_suggestions(document)

        self.assertEqual(document.ai_document_type, "Policy")
        self.assertEqual(document.ai_explanation["document_type"]["confidence"], "high")
        self.assertEqual(document.ai_suggestion_status, "suggested")
        self.assertEqual(document.ai_suggested_at, suggested_at)
        self.assertEqual(document.save.call_count, 2)
        self.assertIn(
            "ai_explanation",
            document.save.call_args_list[1].kwargs["update_fields"],
        )


class MetadataQualityServiceTests(SimpleTestCase):
    def build_document(self):
        return Mock(
            document_type="Policy",
            document_subtype="Benefits",
            department="Finance",
            author="",
            description="Employee health policy",
            tags="benefits",
            extracted_text=(
                "Eligible employees may enroll in health coverage. "
                "Human Resources administers the benefits program."
            ),
        )

    def test_prompt_compares_current_metadata_with_document_content(self):
        prompt = build_metadata_quality_prompt(
            self.build_document(),
            "Eligible employees may enroll.",
        )

        self.assertIn('"department": "Finance"', prompt)
        self.assertIn("accurate, relevant, and useful", prompt)
        self.assertIn("populated values that conflict", prompt)
        self.assertIn("Eligible employees may enroll.", prompt)

    def test_parse_quality_response_returns_grounded_findings(self):
        result = parse_metadata_quality_response(
            json.dumps({
                "quality_score": 58,
                "summary": "Department and tags need review.",
                "issues": [
                    {
                        "field": "department",
                        "current_value": "Finance",
                        "suggested_value": "Human Resources",
                        "severity": "high",
                        "reason": "The document describes employee benefits.",
                        "evidence": (
                            "Human Resources administers the benefits program."
                        ),
                    },
                    {
                        "field": "tags",
                        "current_value": "benefits",
                        "suggested_value": "benefits, health, enrollment",
                        "severity": "medium",
                        "reason": "The current tags omit key topics.",
                        "evidence": (
                            "Eligible employees may enroll in health coverage."
                        ),
                    },
                ],
            }),
            self.build_document().extracted_text,
        )

        self.assertEqual(result["quality_score"], 58)
        self.assertEqual(result["quality_level"], "needs_review")
        self.assertEqual(result["issues"][0]["field_label"], "Department")
        self.assertEqual(
            result["issues"][0]["suggested_value"],
            "Human Resources",
        )

    def test_parse_quality_response_removes_unmatched_evidence(self):
        result = parse_metadata_quality_response(
            json.dumps({
                "quality_score": 80,
                "summary": "Mostly accurate.",
                "issues": [
                    {
                        "field": "department",
                        "current_value": "Finance",
                        "suggested_value": "HR",
                        "severity": "medium",
                        "reason": "Content mismatch.",
                        "evidence": "Invented source sentence.",
                    }
                ],
            }),
            "Real source sentence.",
        )

        self.assertEqual(result["issues"][0]["evidence"], "")

    def test_quality_level_is_normalized_from_score(self):
        self.assertEqual(quality_level_for_score(95), "excellent")
        self.assertEqual(quality_level_for_score(80), "good")
        self.assertEqual(quality_level_for_score(60), "needs_review")
        self.assertEqual(quality_level_for_score(20), "critical")

    @override_settings(
        AI_METADATA_MAX_CHARS=80,
        AWS_REGION="us-east-1",
        BEDROCK_NOVA_MODEL_ID="amazon.nova-lite-v1:0",
        BEDROCK_TIMEOUT_SECONDS=30,
    )
    @patch("documents.metadata_quality.boto3.client")
    def test_review_document_metadata_calls_bedrock(self, mock_client):
        client = Mock()
        client.invoke_model.return_value = {
            "body": BytesIO(json.dumps({
                "output": {
                    "message": {
                        "content": [{
                            "text": json.dumps({
                                "quality_score": 91,
                                "summary": "Metadata is accurate.",
                                "issues": [],
                            }),
                        }],
                    },
                },
            }).encode("utf-8")),
        }
        mock_client.return_value = client

        result = review_document_metadata(self.build_document())

        self.assertEqual(result["quality_level"], "excellent")
        client.invoke_model.assert_called_once()
        body = json.loads(client.invoke_model.call_args.kwargs["body"])
        self.assertEqual(body["inferenceConfig"]["temperature"], 0.1)

    def test_review_document_metadata_requires_extracted_text(self):
        document = self.build_document()
        document.extracted_text = ""

        with self.assertRaisesMessage(
            MetadataQualityError,
            "No extracted text",
        ):
            review_document_metadata(document)


class MetadataQualityViewTests(SimpleTestCase):
    def get_loader_request(self, method="GET", query=None):
        return Mock(
            method=method,
            GET=query or {},
            session={
                "user": {
                    "groups": ["DocumentLoader"],
                    "name": "Loader",
                    "email": "loader@example.com",
                },
            },
        )

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
        DOCUMENTS_PER_PAGE=10,
    )
    @patch("documents.views.render")
    @patch("documents.views.MetadataQualityReview.objects.all")
    @patch("documents.views.Document.objects.order_by")
    def test_dashboard_reports_stored_review_summary(
        self,
        mock_documents,
        mock_reviews,
        mock_render,
    ):
        documents = [
            Mock(id=1),
            Mock(id=2),
            Mock(id=3),
        ]
        reviews = [
            Mock(
                document_id=1,
                status="complete",
                quality_score=90,
                quality_level="excellent",
            ),
            Mock(
                document_id=2,
                status="complete",
                quality_score=50,
                quality_level="needs_review",
            ),
        ]
        mock_documents.return_value = documents
        mock_reviews.return_value = reviews
        mock_render.return_value = Mock()

        response = metadata_quality_dashboard(self.get_loader_request())

        self.assertEqual(response, mock_render.return_value)
        context = mock_render.call_args.args[2]
        self.assertEqual(context["total_documents"], 3)
        self.assertEqual(context["reviewed_count"], 2)
        self.assertEqual(context["unreviewed_count"], 1)
        self.assertEqual(context["average_score"], 70)
        self.assertEqual(context["level_counts"]["needs_review"], 1)

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
        BEDROCK_NOVA_MODEL_ID="amazon.nova-lite-v1:0",
    )
    @patch("documents.views.messages.success")
    @patch("documents.views.record_audit_event")
    @patch("documents.views.redirect")
    @patch("documents.views.MetadataQualityReview.objects.update_or_create")
    @patch("documents.views.review_document_metadata")
    @patch("documents.views.Document.objects.get")
    def test_run_review_persists_result_without_changing_document(
        self,
        mock_get,
        mock_review,
        mock_update,
        mock_redirect,
        mock_audit,
        mock_success,
    ):
        document = Mock(id=42)
        review = Mock(
            quality_score=58,
            quality_level="needs_review",
            issue_count=2,
            model_id="amazon.nova-lite-v1:0",
        )
        mock_get.return_value = document
        mock_review.return_value = {
            "quality_score": 58,
            "quality_level": "needs_review",
            "summary": "Department needs review.",
            "issues": [{"field": "department"}],
        }
        mock_update.return_value = (review, True)
        mock_redirect.return_value = Mock()

        response = run_metadata_quality_review(
            self.get_loader_request(method="POST"),
            42,
        )

        self.assertEqual(response, mock_redirect.return_value)
        defaults = mock_update.call_args.kwargs["defaults"]
        self.assertEqual(defaults["quality_score"], 58)
        self.assertEqual(defaults["issues"], [{"field": "department"}])
        document.save.assert_not_called()
        mock_audit.assert_called_once()
        self.assertEqual(
            mock_audit.call_args.args[3]["source"],
            "ai_metadata_quality_review",
        )
        mock_success.assert_called_once()

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
        BEDROCK_NOVA_MODEL_ID="amazon.nova-lite-v1:0",
    )
    @patch("documents.views.messages.error")
    @patch("documents.views.redirect")
    @patch("documents.views.MetadataQualityReview.objects.update_or_create")
    @patch("documents.views.review_document_metadata")
    @patch("documents.views.Document.objects.get")
    def test_run_review_persists_provider_failure(
        self,
        mock_get,
        mock_review,
        mock_update,
        mock_redirect,
        mock_error,
    ):
        mock_get.return_value = Mock(id=42)
        mock_review.side_effect = MetadataQualityError("Bedrock unavailable")
        mock_redirect.return_value = Mock()

        run_metadata_quality_review(
            self.get_loader_request(method="POST"),
            42,
        )

        defaults = mock_update.call_args.kwargs["defaults"]
        self.assertEqual(defaults["status"], "failed")
        self.assertEqual(defaults["error"], "Bedrock unavailable")
        mock_error.assert_called_once()

    @patch("documents.views.MetadataQualityReview.objects.filter")
    def test_metadata_change_invalidates_stored_quality_review(self, mock_filter):
        document = Mock(id=42)

        invalidate_metadata_quality_review(document)

        mock_filter.assert_called_once_with(document_id=42)
        mock_filter.return_value.delete.assert_called_once()

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
        AI_METADATA_QUALITY_BATCH_LIMIT=3,
    )
    @patch("documents.views.messages.warning")
    @patch("documents.views.messages.success")
    @patch("documents.views.redirect")
    @patch("documents.views.perform_metadata_quality_review")
    @patch("documents.views.Document.objects.in_bulk")
    def test_selected_reviews_process_batch_and_report_results(
        self,
        mock_in_bulk,
        mock_perform,
        mock_redirect,
        mock_success,
        mock_warning,
    ):
        documents = {
            1: Mock(id=1),
            2: Mock(id=2),
            3: Mock(id=3),
        }
        mock_in_bulk.return_value = documents
        mock_perform.side_effect = [
            (Mock(), ""),
            (None, "Bedrock unavailable"),
            (Mock(), ""),
        ]
        mock_redirect.return_value = Mock()
        request = self.get_loader_request(method="POST")
        request.POST = Mock()
        request.POST.getlist.return_value = ["1", "2", "3"]

        response = run_selected_metadata_quality_reviews(request)

        self.assertEqual(response, mock_redirect.return_value)
        mock_in_bulk.assert_called_once_with([1, 2, 3])
        self.assertEqual(mock_perform.call_count, 3)
        self.assertIn("2 documents", mock_success.call_args.args[1])
        self.assertIn("1 document review", mock_warning.call_args.args[1])

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
        AI_METADATA_QUALITY_BATCH_LIMIT=3,
    )
    @patch("documents.views.messages.warning")
    @patch("documents.views.messages.success")
    @patch("documents.views.redirect")
    @patch("documents.views.perform_metadata_quality_review")
    @patch("documents.views.Document.objects.in_bulk")
    def test_selected_reviews_limit_large_synchronous_batch(
        self,
        mock_in_bulk,
        mock_perform,
        mock_redirect,
        mock_success,
        mock_warning,
    ):
        mock_in_bulk.return_value = {
            1: Mock(id=1),
            2: Mock(id=2),
            3: Mock(id=3),
        }
        mock_perform.return_value = (Mock(), "")
        request = self.get_loader_request(method="POST")
        request.POST = Mock()
        request.POST.getlist.return_value = ["1", "2", "3", "4"]

        run_selected_metadata_quality_reviews(request)

        mock_in_bulk.assert_called_once_with([1, 2, 3])
        self.assertEqual(mock_perform.call_count, 3)
        self.assertIn("up to 3", mock_warning.call_args_list[0].args[1])
        mock_success.assert_called_once()


class EmbeddingTests(SimpleTestCase):
    def test_chunk_text_splits_paragraph_aware_chunks(self):
        chunks = chunk_text("First paragraph.\n\nSecond paragraph.", max_chars=25)

        self.assertEqual(chunks, ["First paragraph.", "Second paragraph."])

    def test_chunk_text_splits_large_paragraphs(self):
        chunks = chunk_text("abcdefghij", max_chars=4)

        self.assertEqual(chunks, ["abcd", "efgh", "ij"])

    @override_settings(
        AWS_REGION="us-east-1",
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        BEDROCK_TIMEOUT_SECONDS=45,
    )
    @patch("documents.embeddings.boto3.client")
    def test_get_titan_embedding_calls_bedrock(self, mock_client):
        embedding = [0.1] * 1024
        client = Mock()
        client.invoke_model.return_value = {
            "body": BytesIO(json.dumps({"embedding": embedding}).encode("utf-8"))
        }
        mock_client.return_value = client

        result = get_titan_embedding("Chunk text")

        self.assertEqual(len(result), 1024)
        self.assertEqual(result, embedding)
        mock_client.assert_called_once()
        client_args, client_kwargs = mock_client.call_args
        self.assertEqual(client_args[0], "bedrock-runtime")
        self.assertEqual(client_kwargs["region_name"], "us-east-1")
        _, invoke_kwargs = client.invoke_model.call_args
        self.assertEqual(
            invoke_kwargs["modelId"],
            "amazon.titan-embed-text-v2:0",
        )
        self.assertEqual(
            json.loads(invoke_kwargs["body"]),
            {"inputText": "Chunk text"},
        )


class DocumentEmbeddingTests(SimpleTestCase):
    @override_settings(
        AI_EMBEDDING_MAX_CHARS=25,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
    )
    @patch("documents.embeddings.get_titan_embedding")
    @patch("documents.embeddings.DocumentChunk.objects.create")
    def test_rebuild_document_embeddings_creates_chunks(
        self,
        mock_create,
        mock_embedding,
    ):
        mock_embedding.return_value = [0.1] * 1024
        existing_chunks = Mock()
        document = Mock(
            extracted_text="First paragraph.\n\nSecond paragraph.",
        )
        document.chunks.all.return_value = existing_chunks

        created = rebuild_document_embeddings(document)

        self.assertEqual(created, 2)
        existing_chunks.delete.assert_called_once()
        self.assertEqual(mock_create.call_count, 2)
        first_create = mock_create.call_args_list[0].kwargs
        self.assertEqual(first_create["document"], document)
        self.assertEqual(first_create["chunk_index"], 0)
        self.assertEqual(
            first_create["chunk_text"],
            "First paragraph.",
        )
        self.assertEqual(len(first_create["embedding"]), 1024)
        self.assertEqual(
            first_create["embedding_model"],
            "amazon.titan-embed-text-v2:0",
        )

    @patch("documents.embeddings.get_titan_embedding")
    @patch("documents.embeddings.DocumentChunk.objects.create")
    def test_rebuild_document_embeddings_skips_empty_or_failed_text(
        self,
        mock_create,
        mock_embedding,
    ):
        empty_chunks = Mock()
        empty_document = Mock(extracted_text="")
        empty_document.chunks.all.return_value = empty_chunks
        failed_chunks = Mock()
        failed_document = Mock(extracted_text="TEXT_EXTRACTION_FAILED: OCR failed")
        failed_document.chunks.all.return_value = failed_chunks

        self.assertEqual(rebuild_document_embeddings(empty_document), 0)
        self.assertEqual(rebuild_document_embeddings(failed_document), 0)
        empty_chunks.delete.assert_called_once()
        failed_chunks.delete.assert_called_once()
        mock_embedding.assert_not_called()
        mock_create.assert_not_called()


class UploadEmbeddingHookTests(SimpleTestCase):
    @patch("documents.views.rebuild_document_embeddings")
    @patch("documents.views.messages.warning")
    def test_embedding_failure_warns_without_raising(
        self,
        mock_warning,
        mock_rebuild,
    ):
        mock_rebuild.side_effect = EmbeddingError("AWS failed")
        request = Mock()
        document = Mock(extracted_text="Valid extracted text")

        rebuilt = try_rebuild_document_embeddings(request, document)

        self.assertFalse(rebuilt)
        mock_rebuild.assert_called_once_with(document)
        mock_warning.assert_called_once()

    @patch("documents.views.rebuild_document_embeddings")
    def test_embedding_hook_skips_failed_extraction(self, mock_rebuild):
        request = Mock()
        document = Mock(extracted_text="TEXT_EXTRACTION_FAILED: OCR failed")

        rebuilt = try_rebuild_document_embeddings(request, document)

        self.assertFalse(rebuilt)
        mock_rebuild.assert_not_called()

    @override_settings(MCP_INDEXING_ENABLED=False)
    @patch("documents.views.try_reindex_document")
    @patch("documents.views.try_rebuild_document_embeddings")
    def test_upload_search_index_uses_direct_path_by_default(
        self,
        mock_rebuild,
        mock_reindex,
    ):
        request = Mock()
        document = Mock()
        mock_rebuild.return_value = True
        mock_reindex.return_value = True

        indexed = try_index_document_for_search(request, document)

        self.assertTrue(indexed)
        mock_rebuild.assert_called_once_with(request, document)
        mock_reindex.assert_called_once_with(request, document)

    @override_settings(
        MCP_INDEXING_ENABLED=True,
        OPENSEARCH_INDEX_ON_SAVE=True,
    )
    @patch("documents.views.try_mcp_index_document")
    @patch("documents.views.try_reindex_document")
    @patch("documents.views.try_rebuild_document_embeddings")
    def test_upload_search_index_uses_mcp_when_enabled(
        self,
        mock_rebuild,
        mock_reindex,
        mock_mcp,
    ):
        request = Mock()
        document = Mock()
        mock_mcp.return_value = True

        indexed = try_index_document_for_search(request, document)

        self.assertTrue(indexed)
        mock_mcp.assert_called_once_with(
            request,
            document,
            replace_existing_chunks=True,
        )
        mock_rebuild.assert_not_called()
        mock_reindex.assert_not_called()


class OpenSearchIndexingTests(SimpleTestCase):
    def test_build_document_payload_uses_canonical_document_fields(self):
        uploaded_at = SimpleNamespace(
            isoformat=Mock(return_value="2026-05-30T12:00:00+00:00")
        )
        document = Mock(
            id=42,
            file=SimpleNamespace(name="documents/policy.pdf"),
            document_type="Policy",
            document_subtype="HR",
            department="People",
            author="Admin",
            description="Benefits policy",
            tags="benefits, hr",
            extracted_text="Full extracted policy text",
            ocr_text="OCR policy text",
            uploaded_at=uploaded_at,
            ai_document_type="Policy",
            ai_department="People",
            ai_tags="benefits",
            ai_summary="Policy summary",
            ai_metadata_provider="bedrock",
            ai_suggestion_status="suggested",
        )

        payload = build_document_payload(document)

        self.assertEqual(payload["document_id"], 42)
        self.assertEqual(payload["file_name"], "documents/policy.pdf")
        self.assertEqual(payload["document_type"], "Policy")
        self.assertEqual(payload["ai_summary"], "Policy summary")
        self.assertEqual(payload["tags_list"], ["benefits", "hr"])
        self.assertEqual(payload["ai_tags_list"], ["benefits"])
        self.assertEqual(payload["extracted_text"], "Full extracted policy text")
        self.assertEqual(payload["ocr_text"], "OCR policy text")
        self.assertIn("Full extracted policy text", payload["searchable_text"])
        self.assertIn("Policy summary", payload["searchable_text"])
        self.assertEqual(payload["uploaded_at"], "2026-05-30T12:00:00+00:00")

    def test_build_chunk_payload_denormalizes_document_metadata(self):
        document = Mock(
            id=42,
            file=SimpleNamespace(name="documents/policy.pdf"),
            document_type="Policy",
            document_subtype="HR",
            department="People",
            author="Admin",
            tags="benefits, hr",
            ai_document_type="Policy",
            ai_department="People",
            ai_tags="benefits",
            ai_summary="Policy summary",
            ai_metadata_provider="bedrock",
            ai_suggestion_status="suggested",
            uploaded_at=None,
        )
        chunk = Mock(
            id=99,
            document=document,
            chunk_index=3,
            chunk_text="Chunk text",
            embedding=[0.1, 0.2],
            embedding_model="amazon.titan-embed-text-v2:0",
        )

        payload = build_chunk_payload(chunk)

        self.assertEqual(payload["document_id"], 42)
        self.assertEqual(payload["chunk_id"], 99)
        self.assertEqual(payload["chunk_index"], 3)
        self.assertEqual(payload["department"], "People")
        self.assertEqual(payload["embedding"], [0.1, 0.2])
        self.assertEqual(payload["tags_list"], ["benefits", "hr"])
        self.assertEqual(payload["ai_tags_list"], ["benefits"])
        self.assertEqual(payload["ai_department"], "People")
        self.assertIn("Chunk text", payload["searchable_text"])
        self.assertIn("Policy summary", payload["searchable_text"])

    @override_settings(AI_EMBEDDING_DIMENSIONS=1024)
    def test_mappings_include_searchable_text_metadata_and_embeddings(self):
        document_properties = get_document_mapping()["mappings"]["properties"]
        chunk_properties = get_chunk_mapping()["mappings"]["properties"]

        self.assertEqual(document_properties["extracted_text"]["type"], "text")
        self.assertEqual(document_properties["ocr_text"]["type"], "text")
        self.assertEqual(document_properties["department"]["type"], "keyword")
        self.assertEqual(document_properties["tags_list"]["type"], "keyword")
        self.assertEqual(chunk_properties["document_id"]["type"], "integer")
        self.assertEqual(chunk_properties["chunk_text"]["type"], "text")
        self.assertEqual(chunk_properties["embedding"]["type"], "knn_vector")
        self.assertEqual(chunk_properties["embedding"]["dimension"], 1024)
        self.assertEqual(chunk_properties["ai_department"]["type"], "keyword")
        self.assertEqual(chunk_properties["ai_tags_list"]["type"], "keyword")

    def test_build_metadata_filter_query_applies_filter_clauses(self):
        query = build_metadata_filter_query({
            "document_type": "Policy",
            "department": "People",
            "author": "Admin",
            "tags": "benefits",
            "uploaded_from": "2026-01-01",
            "uploaded_to": "2026-12-31",
        })

        clauses = query["bool"]["filter"]

        self.assertIn({"term": {"document_type": "Policy"}}, clauses)
        self.assertIn({"term": {"department": "People"}}, clauses)
        self.assertIn({"term": {"author.raw": "Admin"}}, clauses)
        self.assertIn({"term": {"tags_list": "benefits"}}, clauses)
        self.assertIn(
            {
                "range": {
                    "uploaded_at": {
                        "gte": "2026-01-01",
                        "lte": "2026-12-31",
                    }
                }
            },
            clauses,
        )

    def test_build_metadata_filter_query_returns_match_all_without_filters(self):
        self.assertEqual(build_metadata_filter_query({}), {"match_all": {}})

    @override_settings(
        OPENSEARCH_DOCUMENT_INDEX="docmanager-documents",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    def test_ensure_indexes_creates_missing_indexes(self):
        client = Mock()
        client.indices.exists.side_effect = [False, False]

        ensure_indexes(client=client)

        self.assertEqual(client.indices.create.call_count, 2)
        created_indexes = [
            call.kwargs["index"]
            for call in client.indices.create.call_args_list
        ]
        self.assertEqual(
            created_indexes,
            ["docmanager-documents", "docmanager-document-chunks"],
        )

    @override_settings(
        OPENSEARCH_DOCUMENT_INDEX="docmanager-documents",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    def test_ensure_indexes_updates_existing_index_mappings(self):
        client = Mock()
        client.indices.exists.side_effect = [True, True]

        ensure_indexes(client=client)

        self.assertEqual(client.indices.put_mapping.call_count, 2)
        updated_indexes = [
            call.kwargs["index"]
            for call in client.indices.put_mapping.call_args_list
        ]
        self.assertEqual(
            updated_indexes,
            ["docmanager-documents", "docmanager-document-chunks"],
        )
        self.assertIn(
            "searchable_text",
            client.indices.put_mapping.call_args_list[0].kwargs["body"]["properties"],
        )

    @override_settings(OPENSEARCH_DOCUMENT_INDEX="docmanager-documents")
    def test_index_document_wraps_client_errors(self):
        client = Mock()
        client.index.side_effect = RuntimeError("connection failed")
        document = Mock(id=42, file=None)

        with self.assertRaises(OpenSearchIndexingError):
            index_document(document, client=client)

    @override_settings(
        OPENSEARCH_DOCUMENT_INDEX="docmanager-documents",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    def test_delete_document_removes_document_and_chunks(self):
        client = Mock()

        delete_indexed_document(42, client=client)

        client.delete.assert_called_once_with(
            index="docmanager-documents",
            id="42",
            ignore=[404],
            refresh=False,
        )
        client.delete_by_query.assert_called_once()
        self.assertEqual(
            client.delete_by_query.call_args.kwargs["body"],
            {"query": {"term": {"document_id": 42}}},
        )


class OpenSearchViewHookTests(SimpleTestCase):
    @override_settings(OPENSEARCH_INDEX_ON_SAVE=True)
    @patch("documents.views.reindex_document")
    def test_reindex_hook_indexes_document_when_enabled(self, mock_reindex):
        request = Mock()
        document = Mock()

        indexed = try_reindex_document(request, document)

        self.assertTrue(indexed)
        mock_reindex.assert_called_once_with(document, create_indexes=True)

    @override_settings(OPENSEARCH_INDEX_ON_SAVE=True)
    @patch("documents.views.messages.warning")
    @patch("documents.views.reindex_document")
    def test_reindex_hook_warns_without_raising(
        self,
        mock_reindex,
        mock_warning,
    ):
        mock_reindex.side_effect = OpenSearchIndexingError("unavailable")
        request = Mock()
        document = Mock()

        indexed = try_reindex_document(request, document)

        self.assertFalse(indexed)
        mock_warning.assert_called_once()

    @override_settings(
        MCP_INDEXING_ENABLED=True,
        OPENSEARCH_INDEX_ON_SAVE=True,
    )
    @patch("documents.views.mcp_index_document")
    @patch("documents.views.reindex_document")
    def test_reindex_hook_uses_mcp_without_rebuilding_chunks(
        self,
        mock_reindex,
        mock_mcp,
    ):
        request = Mock()
        document = Mock(
            id=42,
            file=SimpleNamespace(name="documents/policy.pdf"),
        )
        mock_mcp.return_value = {"status": "ok"}

        indexed = try_reindex_document(request, document)

        self.assertTrue(indexed)
        mock_reindex.assert_not_called()
        payload = mock_mcp.call_args.args[0]
        self.assertEqual(payload["document"]["document_id"], 42)
        self.assertEqual(
            payload["document"]["file_name"],
            "documents/policy.pdf",
        )
        self.assertFalse(payload["options"]["replace_existing_chunks"])
        self.assertTrue(payload["options"]["index_document_metadata"])
        self.assertTrue(payload["options"]["index_chunks"])
        self.assertEqual(payload["trace"]["source"], "django-view")
        self.assertTrue(payload["trace"]["request_id"])

    @override_settings(
        MCP_INDEXING_ENABLED=True,
        OPENSEARCH_INDEX_ON_SAVE=True,
    )
    @patch("documents.views.messages.warning")
    @patch("documents.views.mcp_index_document")
    def test_reindex_hook_warns_on_mcp_error_response(
        self,
        mock_mcp,
        mock_warning,
    ):
        request = Mock()
        document = Mock(id=42, file=None)
        mock_mcp.return_value = {
            "status": "error",
            "error": {"code": "indexing_unavailable"},
        }

        indexed = try_reindex_document(request, document)

        self.assertFalse(indexed)
        mock_warning.assert_called_once()


class BulkImportMCPIndexingTests(SimpleTestCase):
    def build_document(self):
        return Mock(
            id=42,
            file=SimpleNamespace(name="documents/policy.txt"),
        )

    @override_settings(MCP_INDEXING_ENABLED=True)
    @patch("documents.management.commands.bulk_import_documents.reindex_document")
    @patch(
        "documents.management.commands.bulk_import_documents."
        "rebuild_document_embeddings"
    )
    @patch("documents.management.commands.bulk_import_documents.get_opensearch_client")
    @patch("documents.management.commands.bulk_import_documents.mcp_index_document")
    @patch(
        "documents.management.commands.bulk_import_documents."
        "Command.import_file"
    )
    @patch(
        "documents.management.commands.bulk_import_documents."
        "Command.get_supported_files"
    )
    def test_bulk_import_uses_mcp_indexing_when_enabled(
        self,
        mock_supported_files,
        mock_import_file,
        mock_mcp,
        mock_client,
        mock_rebuild,
        mock_reindex,
    ):
        mock_supported_files.return_value = [Path("policy.txt")]
        mock_import_file.return_value = self.build_document()
        mock_mcp.return_value = {
            "status": "ok",
            "summary": {
                "chunks_created": 2,
                "chunk_records_indexed": 2,
            },
        }
        stdout = StringIO()

        with TemporaryDirectory() as source_dir:
            call_command(
                "bulk_import_documents",
                source_dir,
                "--rebuild-embeddings",
                "--reindex-opensearch",
                "--create-indexes",
                stdout=stdout,
            )

        output = stdout.getvalue()
        self.assertIn("2 chunks embedded", output)
        self.assertIn("2 chunks indexed", output)
        self.assertIn("embedded=1 indexed=1", output)
        payload = mock_mcp.call_args.args[0]
        self.assertEqual(payload["document"]["document_id"], 42)
        self.assertEqual(payload["document"]["file_name"], "documents/policy.txt")
        self.assertTrue(payload["options"]["replace_existing_chunks"])
        self.assertTrue(payload["options"]["index_document_metadata"])
        self.assertTrue(payload["options"]["index_chunks"])
        self.assertEqual(payload["trace"]["source"], "bulk_import_documents")
        mock_client.assert_not_called()
        mock_rebuild.assert_not_called()
        mock_reindex.assert_not_called()

    @override_settings(MCP_INDEXING_ENABLED=False)
    @patch("documents.management.commands.bulk_import_documents.ensure_indexes")
    @patch("documents.management.commands.bulk_import_documents.get_opensearch_client")
    @patch("documents.management.commands.bulk_import_documents.reindex_document")
    @patch(
        "documents.management.commands.bulk_import_documents."
        "rebuild_document_embeddings"
    )
    @patch("documents.management.commands.bulk_import_documents.mcp_index_document")
    @patch(
        "documents.management.commands.bulk_import_documents."
        "Command.import_file"
    )
    @patch(
        "documents.management.commands.bulk_import_documents."
        "Command.get_supported_files"
    )
    def test_bulk_import_keeps_direct_indexing_when_mcp_disabled(
        self,
        mock_supported_files,
        mock_import_file,
        mock_mcp,
        mock_rebuild,
        mock_reindex,
        mock_client,
        mock_ensure,
    ):
        client = Mock()
        document = self.build_document()
        mock_supported_files.return_value = [Path("policy.txt")]
        mock_import_file.return_value = document
        mock_client.return_value = client
        mock_rebuild.return_value = 2
        mock_reindex.return_value = 2
        stdout = StringIO()

        with TemporaryDirectory() as source_dir:
            call_command(
                "bulk_import_documents",
                source_dir,
                "--rebuild-embeddings",
                "--reindex-opensearch",
                "--create-indexes",
                stdout=stdout,
            )

        self.assertIn("embedded=1 indexed=1", stdout.getvalue())
        mock_mcp.assert_not_called()
        mock_rebuild.assert_called_once_with(document)
        mock_reindex.assert_called_once_with(document, client=client)
        mock_ensure.assert_called_once_with(client=client)


class SourceOfTruthViewFlowTests(SimpleTestCase):
    def get_loader_request(self):
        return Mock(
            method="POST",
            session={
                "user": {
                    "groups": ["DocumentLoader"],
                    "name": "Loader",
                    "email": "loader@example.com",
                }
            },
        )

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
    )
    @patch("documents.views.messages.success")
    @patch("documents.views.record_audit_event")
    @patch("documents.views.try_reindex_document")
    @patch("documents.views.invalidate_metadata_quality_review")
    @patch("documents.views.Document.objects.get")
    def test_accept_ai_metadata_saves_postgres_before_reindexing(
        self,
        mock_get,
        mock_invalidate,
        mock_reindex,
        mock_audit,
        mock_success,
    ):
        request = self.get_loader_request()
        document = Mock(
            id=42,
            document_type="Old",
            document_subtype="",
            department="Old department",
            author="Admin",
            tags="old",
            ocr_language="english",
            file=SimpleNamespace(name="documents/policy.pdf"),
            ai_document_type="Policy",
            ai_department="People",
            ai_tags="benefits",
            ai_summary="Policy summary",
        )
        call_order = []
        document.save.side_effect = lambda *args, **kwargs: call_order.append("save")
        document.refresh_from_db.side_effect = lambda: call_order.append("refresh")
        mock_reindex.side_effect = lambda *args, **kwargs: call_order.append("reindex")
        mock_get.return_value = document

        accept_ai_metadata(request, 42)

        self.assertEqual(document.document_type, "Policy")
        self.assertEqual(document.department, "People")
        self.assertEqual(document.tags, "benefits")
        self.assertEqual(document.description, "Policy summary")
        self.assertEqual(call_order, ["save", "refresh", "reindex"])
        mock_invalidate.assert_called_once_with(document)
        mock_reindex.assert_called_once_with(request, document)
        mock_audit.assert_called_once()
        mock_success.assert_called_once()

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
    )
    @patch("documents.views.messages.info")
    @patch("documents.views.record_audit_event")
    @patch("documents.views.try_reindex_document")
    @patch("documents.views.Document.objects.get")
    def test_reject_ai_metadata_saves_postgres_before_reindexing(
        self,
        mock_get,
        mock_reindex,
        mock_audit,
        mock_info,
    ):
        request = self.get_loader_request()
        document = Mock(
            id=42,
            ai_document_type="Policy",
            ai_department="HR",
            ai_tags="benefits",
            ai_summary="Policy summary",
            ai_explanation={
                "document_type": {
                    "confidence": "high",
                    "reason": "The document identifies itself as a policy.",
                    "evidence": "Employee Policy",
                },
            },
        )
        call_order = []
        document.save.side_effect = lambda *args, **kwargs: call_order.append("save")
        document.refresh_from_db.side_effect = lambda: call_order.append("refresh")
        mock_reindex.side_effect = lambda *args, **kwargs: call_order.append("reindex")
        mock_get.return_value = document

        reject_ai_metadata(request, 42)

        self.assertEqual(document.ai_suggestion_status, "rejected")
        self.assertEqual(call_order, ["save", "refresh", "reindex"])
        document.save.assert_called_once_with(update_fields=["ai_suggestion_status"])
        mock_reindex.assert_called_once_with(request, document)
        mock_audit.assert_called_once()
        audit_metadata = mock_audit.call_args.args[3]
        self.assertEqual(audit_metadata["source"], "ai_metadata_reject")
        self.assertEqual(
            audit_metadata["ai_suggestion"]["explanation"]["document_type"][
                "confidence"
            ],
            "high",
        )
        mock_info.assert_called_once()


class ReindexOpenSearchCommandTests(SimpleTestCase):
    @patch("documents.management.commands.reindex_opensearch.reindex_document")
    @patch("documents.management.commands.reindex_opensearch.ensure_indexes")
    @patch("documents.management.commands.reindex_opensearch.get_opensearch_client")
    @patch("documents.management.commands.reindex_opensearch.Document")
    def test_reindex_command_processes_documents(
        self,
        mock_document_model,
        mock_get_client,
        mock_ensure_indexes,
        mock_reindex,
    ):
        from documents.management.commands.reindex_opensearch import Command

        document = Mock(id=42, file=SimpleNamespace(name="documents/a.pdf"))
        queryset = MagicMock()
        mock_document_model.objects.all.return_value.order_by.return_value = queryset
        queryset.__iter__.return_value = iter([document])
        client = Mock()
        mock_get_client.return_value = client
        mock_reindex.return_value = 2

        command = Command()
        command.handle(document_id=None, limit=None, create_indexes=True)

        mock_ensure_indexes.assert_called_once_with(client=client)
        mock_reindex.assert_called_once_with(document, client=client)


class ValidateBedrockOpenSearchCommandTests(SimpleTestCase):
    @override_settings(
        AWS_REGION="us-east-1",
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        AI_EMBEDDING_DIMENSIONS=1024,
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch.dict(
        "os.environ",
        {
            "AWS_ACCESS_KEY_ID": "test-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
        },
    )
    @patch("documents.management.commands.validate_bedrock_opensearch.Document")
    @patch("documents.management.commands.validate_bedrock_opensearch.DocumentChunk")
    @patch(
        "documents.management.commands.validate_bedrock_opensearch."
        "get_opensearch_client"
    )
    @patch(
        "documents.management.commands.validate_bedrock_opensearch."
        "get_titan_embedding"
    )
    def test_validate_command_checks_bedrock_db_and_opensearch(
        self,
        mock_embedding,
        mock_get_client,
        mock_document_chunk,
        mock_document,
    ):
        mock_embedding.return_value = [0.1] * 1024
        chunk = Mock(embedding=[0.2] * 1024)
        queryset = MagicMock()
        queryset.count.return_value = 1
        queryset.iterator.return_value = iter([chunk])
        mock_document_chunk.objects.exclude.return_value.filter.return_value = queryset
        mock_document.objects.filter.return_value.exists.return_value = True
        client = Mock()
        client.count.return_value = {"count": 1}
        client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "document_id": 42,
                            "chunk_id": 99,
                            "chunk_index": 0,
                            "embedding": [0.3] * 1024,
                            "embedding_model": "amazon.titan-embed-text-v2:0",
                        }
                    }
                ]
            }
        }
        mock_get_client.return_value = client
        stdout = StringIO()

        call_command("validate_bedrock_opensearch", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("Bedrock sample embedding dimensions=1024", output)
        self.assertIn("db_chunks_with_embeddings=1", output)
        self.assertIn("os_chunks=1", output)
        self.assertIn("validation succeeded", output)
        client.search.assert_called_once()
        mock_document.objects.filter.assert_called_once_with(id=42)

    @patch(
        "documents.management.commands.validate_bedrock_opensearch."
        "get_titan_embedding"
    )
    def test_validate_command_reports_bedrock_failures(self, mock_embedding):
        mock_embedding.side_effect = EmbeddingError("Unable to reach Bedrock")

        with self.assertRaises(CommandError):
            call_command("validate_bedrock_opensearch", stdout=StringIO())


class HealthAiSearchCommandTests(SimpleTestCase):
    @override_settings(
        AWS_REGION="us-east-1",
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        AI_EMBEDDING_DIMENSIONS=1024,
        OPENSEARCH_URL="http://opensearch:9200",
        OPENSEARCH_DOCUMENT_INDEX="docmanager-documents",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch.dict(
        "os.environ",
        {
            "AWS_ACCESS_KEY_ID": "test-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
        },
    )
    @patch("documents.management.commands.health_ai_search.get_titan_embedding")
    @patch("documents.management.commands.health_ai_search.get_opensearch_client")
    @patch("documents.management.commands.health_ai_search.DocumentChunk")
    @patch("documents.management.commands.health_ai_search.Document")
    def test_health_ai_search_reports_ok_status(
        self,
        mock_document,
        mock_document_chunk,
        mock_get_client,
        mock_embedding,
    ):
        mock_document.objects.count.return_value = 3
        embedded_chunks = MagicMock()
        embedded_chunks.count.return_value = 5
        embedded_chunks.iterator.return_value = iter([
            Mock(embedding=[0.2] * 1024),
            Mock(embedding=[0.3] * 1024),
        ])
        mock_document_chunk.objects.count.return_value = 5
        mock_document_chunk.objects.exclude.return_value.filter.return_value = (
            embedded_chunks
        )

        client = Mock()
        client.info.return_value = {"version": {"number": "3.3.0"}}
        client.indices.exists.side_effect = [True, True]
        client.count.side_effect = [{"count": 3}, {"count": 5}]
        mock_get_client.return_value = client
        mock_embedding.return_value = [0.1] * 1024
        stdout = StringIO()

        call_command("health_ai_search", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("settings=ok", output)
        self.assertIn("postgres=ok documents=3 chunks=5", output)
        self.assertIn("embeddings=ok chunks_with_embeddings=5", output)
        self.assertIn("opensearch=ok version=3.3.0", output)
        self.assertIn("bedrock=ok embedding_dimensions=1024", output)
        self.assertIn("summary=done errors=0 warnings=0", output)

    @patch("documents.management.commands.health_ai_search.get_opensearch_client")
    @patch("documents.management.commands.health_ai_search.DocumentChunk")
    @patch("documents.management.commands.health_ai_search.Document")
    def test_health_ai_search_reports_opensearch_failures(
        self,
        mock_document,
        mock_document_chunk,
        mock_get_client,
    ):
        mock_document.objects.count.return_value = 1
        embedded_chunks = MagicMock()
        embedded_chunks.count.return_value = 0
        embedded_chunks.iterator.return_value = iter([])
        mock_document_chunk.objects.count.return_value = 0
        mock_document_chunk.objects.exclude.return_value.filter.return_value = (
            embedded_chunks
        )
        mock_get_client.side_effect = RuntimeError("connection refused")
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command("health_ai_search", "--skip-bedrock", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("opensearch=error connection_failed=connection refused", output)
        self.assertIn("bedrock=warn skipped live embedding check", output)
        self.assertIn("summary=done errors=1", output)


class HealthDocumentIntelligenceCommandTests(SimpleTestCase):
    @override_settings(
        AWS_REGION="us-east-1",
        BEDROCK_NOVA_MODEL_ID="amazon.nova-lite-v1:0",
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        BEDROCK_TIMEOUT_SECONDS=30,
        AI_EMBEDDING_DIMENSIONS=1024,
        AI_EMBEDDING_MAX_CHARS=2500,
        AI_SEARCH_TOP_K=5,
        AI_RAG_TOP_K=5,
        AI_RAG_MAX_CONTEXT_CHARS=1800,
        AI_RAG_MAX_ANSWER_TOKENS=700,
        AI_RAG_MIN_CONTEXT_CHARS=80,
        OPENSEARCH_URL="http://opensearch:9200",
        OPENSEARCH_TIMEOUT_SECONDS=10,
        OPENSEARCH_DOCUMENT_INDEX="docmanager-documents",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=False,
        MCP_INDEXING_ENABLED=True,
    )
    @patch.dict(
        "os.environ",
        {
            "AWS_ACCESS_KEY_ID": "test-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret",
        },
    )
    @patch(
        "documents.management.commands.health_document_intelligence."
        "generate_answer_with_bedrock"
    )
    @patch(
        "documents.management.commands.health_document_intelligence."
        "get_titan_embedding"
    )
    @patch(
        "documents.management.commands.health_document_intelligence."
        "get_opensearch_client"
    )
    @patch("documents.management.commands.health_document_intelligence.DocumentChunk")
    @patch("documents.management.commands.health_document_intelligence.Document")
    def test_health_document_intelligence_reports_ok_status(
        self,
        mock_document,
        mock_document_chunk,
        mock_get_client,
        mock_embedding,
        mock_answer,
    ):
        mock_document.objects.count.return_value = 7
        embedded_chunks = MagicMock()
        embedded_chunks.count.return_value = 9
        embedded_chunks.iterator.return_value = iter([
            Mock(embedding=[0.2] * 1024),
            Mock(embedding=[0.3] * 1024),
        ])
        mock_document_chunk.objects.count.return_value = 9
        mock_document_chunk.objects.exclude.return_value.filter.return_value = (
            embedded_chunks
        )

        client = Mock()
        client.info.return_value = {"version": {"number": "3.3.0"}}
        client.indices.exists.side_effect = [True, True]
        client.count.side_effect = [{"count": 7}, {"count": 9}]
        mock_get_client.return_value = client
        mock_embedding.return_value = [0.1] * 1024
        mock_answer.return_value = "ok"
        stdout = StringIO()

        call_command("health_document_intelligence", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("runtime=ok", output)
        self.assertIn("limits=ok", output)
        self.assertIn("mcp=ok retrieval_enabled=True", output)
        self.assertIn("postgres=ok documents=7 chunks=9", output)
        self.assertIn("postgres_embeddings=ok chunks_with_embeddings=9", output)
        self.assertIn("opensearch=ok version=3.3.0", output)
        self.assertIn("bedrock_embedding=ok dimensions=1024", output)
        self.assertIn("bedrock_answer=ok non_empty=True", output)
        self.assertIn("summary=done errors=0 warnings=0", output)

    @patch(
        "documents.management.commands.health_document_intelligence."
        "generate_answer_with_bedrock"
    )
    @patch(
        "documents.management.commands.health_document_intelligence."
        "get_titan_embedding"
    )
    @patch(
        "documents.management.commands.health_document_intelligence."
        "get_opensearch_client"
    )
    @patch("documents.management.commands.health_document_intelligence.DocumentChunk")
    @patch("documents.management.commands.health_document_intelligence.Document")
    def test_health_document_intelligence_reports_answer_failures(
        self,
        mock_document,
        mock_document_chunk,
        mock_get_client,
        mock_embedding,
        mock_answer,
    ):
        mock_document.objects.count.return_value = 1
        embedded_chunks = MagicMock()
        embedded_chunks.count.return_value = 1
        embedded_chunks.iterator.return_value = iter([
            Mock(embedding=[0.2] * 1024),
        ])
        mock_document_chunk.objects.count.return_value = 1
        mock_document_chunk.objects.exclude.return_value.filter.return_value = (
            embedded_chunks
        )

        client = Mock()
        client.info.return_value = {"version": {"number": "3.3.0"}}
        client.indices.exists.side_effect = [True, True]
        client.count.side_effect = [{"count": 1}, {"count": 1}]
        mock_get_client.return_value = client
        mock_embedding.return_value = [0.1] * 1024
        mock_answer.side_effect = RAGError("Unable to reach Bedrock")
        stdout = StringIO()

        with self.assertRaises(CommandError):
            call_command("health_document_intelligence", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("bedrock_answer=error failed=Unable to reach Bedrock", output)
        self.assertIn("summary=done errors=1", output)


class SemanticSearchTests(SimpleTestCase):
    def test_cosine_similarity_scores_matching_vectors(self):
        self.assertEqual(cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_cosine_similarity_handles_invalid_vectors(self):
        self.assertEqual(cosine_similarity([], [1, 0]), 0.0)
        self.assertEqual(cosine_similarity([1, 0], [1]), 0.0)
        self.assertEqual(cosine_similarity(["bad"], [1]), 0.0)

    @override_settings(
        AI_SEARCH_TOP_K=5,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.semantic_search.Document.objects.in_bulk")
    @patch("documents.semantic_search.get_opensearch_client")
    @patch("documents.semantic_search.get_titan_embedding")
    def test_search_documents_by_meaning_uses_opensearch_and_hydrates_documents(
        self,
        mock_embedding,
        mock_get_client,
        mock_in_bulk,
    ):
        mock_embedding.return_value = [1, 0]
        document = Mock()
        mock_in_bulk.return_value = {1: document}
        client = Mock()
        client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 1.75,
                        "_source": {
                            "document_id": 1,
                            "chunk_text": "Employee orientation benefits",
                            "embedding_model": "amazon.titan-embed-text-v2:0",
                        },
                    },
                    {
                        "_score": 1.2,
                        "_source": {
                            "document_id": 2,
                            "chunk_text": "Missing PostgreSQL document",
                            "embedding_model": "amazon.titan-embed-text-v2:0",
                        },
                    },
                ]
            }
        }
        mock_get_client.return_value = client

        results = search_documents_by_meaning("employee onboarding")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["document"], document)
        self.assertEqual(results[0]["score"], 1.75)
        self.assertEqual(
            results[0]["best_chunk"],
            "Employee orientation benefits",
        )
        mock_in_bulk.assert_called_once()
        search_body = client.search.call_args.kwargs["body"]
        self.assertEqual(search_body["query"]["knn"]["embedding"]["vector"], [1, 0])
        self.assertEqual(
            search_body["query"]["knn"]["embedding"]["filter"],
            {"term": {"embedding_model": "amazon.titan-embed-text-v2:0"}},
        )

    @override_settings(AI_SEARCH_TOP_K=1)
    @patch("documents.semantic_search.Document.objects.in_bulk")
    @patch("documents.semantic_search.get_opensearch_client")
    @patch("documents.semantic_search.get_titan_embedding")
    def test_search_documents_by_meaning_limits_opensearch_results(
        self,
        mock_embedding,
        mock_get_client,
        mock_in_bulk,
    ):
        mock_embedding.return_value = [1, 0]
        first_document = Mock()
        second_document = Mock()
        mock_in_bulk.return_value = {1: first_document, 2: second_document}
        client = Mock()
        client.search.return_value = {
            "hits": {
                "hits": [
                    {"_score": 2.0, "_source": {"document_id": 1, "chunk_text": "Best"}},
                    {"_score": 1.5, "_source": {"document_id": 2, "chunk_text": "Second"}},
                ]
            }
        }
        mock_get_client.return_value = client

        results = search_documents_by_meaning("employee onboarding")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["document"], first_document)

    @patch("documents.semantic_search.get_opensearch_client")
    @patch("documents.semantic_search.get_titan_embedding")
    def test_search_documents_by_meaning_wraps_opensearch_errors(
        self,
        mock_embedding,
        mock_get_client,
    ):
        mock_embedding.return_value = [1, 0]
        mock_get_client.return_value.search.side_effect = RuntimeError("down")

        with self.assertRaises(EmbeddingError):
            search_documents_by_meaning("employee onboarding")

    @patch("documents.semantic_search.get_opensearch_client")
    @patch("documents.semantic_search.get_titan_embedding")
    def test_search_documents_by_meaning_wraps_opensearch_client_errors(
        self,
        mock_embedding,
        mock_get_client,
    ):
        mock_embedding.return_value = [1, 0]
        mock_get_client.side_effect = OpenSearchIndexingError(
            "opensearch-py is not installed."
        )

        with self.assertRaises(EmbeddingError):
            search_documents_by_meaning("employee onboarding")


class RAGTests(SimpleTestCase):
    @override_settings(
        AI_RAG_CONVERSATION_MAX_TURNS=2,
        AI_RAG_CONVERSATION_MAX_CHARS=1000,
    )
    def test_follow_up_retrieval_query_includes_recent_conversation(self):
        query = build_conversation_retrieval_query(
            "Which of those renew automatically?",
            conversation_history=[
                {
                    "question": "Which contracts expire within 90 days?",
                    "answer": "Alpha and Beta expire within 90 days.",
                },
                {
                    "question": "Who owns them?",
                    "answer": "Legal owns both contracts.",
                },
            ],
        )

        self.assertIn("Which of those renew automatically?", query)
        self.assertIn("Alpha and Beta", query)
        self.assertIn("Legal owns both", query)

    @override_settings(
        AI_RAG_CONVERSATION_MAX_TURNS=1,
        AI_RAG_CONVERSATION_MAX_CHARS=1000,
        AI_RAG_MAX_CONTEXT_CHARS=50,
    )
    def test_rag_prompt_uses_history_only_to_resolve_follow_ups(self):
        document = Mock()
        document.file.name = "documents/contract.pdf"
        document.department = "Legal"
        document.document_type = "Contract"

        prompt = build_rag_prompt(
            "Which of those renew automatically?",
            [{
                "citation_id": 1,
                "document": document,
                "chunk_text": "The agreement renews automatically.",
            }],
            conversation_history=[{
                "question": "Which contracts expire soon?",
                "answer": "The Alpha agreement expires soon [1].",
            }],
        )

        self.assertIn("The Alpha agreement expires soon", prompt)
        self.assertIn("it is not factual evidence", prompt)
        self.assertIn("The agreement renews automatically", prompt)

    @override_settings(
        AI_RAG_TOP_K=2,
        AI_RAG_MIN_RETRIEVAL_SCORE=0,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.rag.get_opensearch_client")
    @patch("documents.rag.get_titan_embedding")
    def test_retrieve_question_context_uses_opensearch_and_hydrates_citations(
        self,
        mock_embedding,
        mock_get_client,
    ):
        mock_embedding.return_value = [1, 0]
        document = Mock()
        document.file.name = "documents/policy.pdf"
        document.department = "HR"
        document.document_type = "Policy"
        accessible_documents = Mock()
        accessible_documents.in_bulk.return_value = {7: document}
        client = Mock()
        client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 1.9,
                        "_source": {
                            "document_id": 7,
                            "chunk_id": 70,
                            "chunk_index": 0,
                            "chunk_text": "Benefits enrollment closes Friday.",
                        },
                    },
                    {
                        "_score": 1.1,
                        "_source": {
                            "document_id": 99,
                            "chunk_text": "Missing document",
                        },
                    },
                ]
            }
        }
        mock_get_client.return_value = client

        contexts = retrieve_question_context(
            "when does enrollment close?",
            documents_queryset=accessible_documents,
        )

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["citation_id"], 1)
        self.assertEqual(contexts[0]["document"], document)
        self.assertEqual(contexts[0]["chunk_id"], 70)
        self.assertEqual(contexts[0]["chunk_text"], "Benefits enrollment closes Friday.")
        search_body = client.search.call_args.kwargs["body"]
        self.assertEqual(search_body["query"]["knn"]["embedding"]["vector"], [1, 0])

    @override_settings(
        AI_RAG_TOP_K=2,
        AI_RAG_MIN_RETRIEVAL_SCORE=0,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.rag.get_opensearch_client")
    @patch("documents.rag.get_titan_embedding")
    def test_retrieve_question_context_hydrates_only_accessible_documents(
        self,
        mock_embedding,
        mock_get_client,
    ):
        mock_embedding.return_value = [1, 0]
        accessible_document = Mock()
        accessible_document.file.name = "documents/allowed.pdf"
        accessible_documents = Mock()
        accessible_documents.in_bulk.return_value = {7: accessible_document}
        client = Mock()
        client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 1.9,
                        "_source": {
                            "document_id": 7,
                            "chunk_index": 0,
                            "chunk_text": "Allowed policy context.",
                        },
                    },
                    {
                        "_score": 1.8,
                        "_source": {
                            "document_id": 8,
                            "chunk_index": 0,
                            "chunk_text": "Restricted policy context.",
                        },
                    },
                ]
            }
        }
        mock_get_client.return_value = client

        contexts = retrieve_question_context(
            "what policies apply?",
            documents_queryset=accessible_documents,
        )

        accessible_documents.in_bulk.assert_called_once_with([7, 8])
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["document"], accessible_document)
        self.assertEqual(contexts[0]["document_id"], 7)

    @override_settings(AI_RAG_MAX_CONTEXT_CHARS=50)
    def test_build_rag_prompt_includes_question_context_and_citation_rules(self):
        document = Mock()
        document.file.name = "documents/policy.pdf"
        document.department = "HR"
        document.document_type = "Policy"
        prompt = build_rag_prompt(
            "What is the deadline?",
            [
                {
                    "citation_id": 1,
                    "document": document,
                    "chunk_text": "Enrollment closes Friday for all employees.",
                }
            ],
        )

        self.assertIn("What is the deadline?", prompt)
        self.assertIn("[1]", prompt)
        self.assertIn("documents/policy.pdf", prompt)
        self.assertIn("using only the provided document excerpts", prompt)

    @override_settings(AI_RAG_MAX_ANSWER_TOKENS=321)
    def test_build_bedrock_rag_request_uses_rag_generation_settings(self):
        request_body = build_bedrock_rag_request("prompt text")

        self.assertEqual(
            request_body["messages"][0]["content"][0]["text"],
            "prompt text",
        )
        self.assertEqual(request_body["inferenceConfig"]["temperature"], 0.2)
        self.assertEqual(request_body["inferenceConfig"]["maxTokens"], 321)

    def test_parse_bedrock_rag_response_returns_answer_text(self):
        payload = {
            "output": {
                "message": {
                    "content": [
                        {"text": " Grounded answer [1]. "},
                    ]
                }
            }
        }

        self.assertEqual(
            parse_bedrock_rag_response(payload),
            "Grounded answer [1].",
        )

    def test_parse_bedrock_rag_response_rejects_invalid_payloads(self):
        with self.assertRaises(RAGError):
            parse_bedrock_rag_response({"output": {}})

        with self.assertRaises(RAGError):
            parse_bedrock_rag_response({
                "output": {"message": {"content": [{"text": "   "}]}}
            })

    @patch("documents.rag.generate_answer_with_bedrock")
    def test_generate_rag_answer_uses_bedrock(self, mock_bedrock):
        mock_bedrock.return_value = "Answer [1]"

        self.assertEqual(generate_rag_answer("prompt"), "Answer [1]")
        mock_bedrock.assert_called_once_with("prompt")

    @patch("documents.rag.generate_rag_answer")
    @patch("documents.rag.retrieve_question_context")
    def test_answer_question_returns_grounded_answer_and_citations(
        self,
        mock_context,
        mock_generate,
    ):
        citation = {
            "citation_id": 1,
            "document": Mock(),
            "chunk_text": "Policy excerpt",
        }
        mock_context.return_value = [citation]
        mock_generate.return_value = "Use the policy [1]."

        with self.settings(AI_RAG_MIN_CONTEXT_CHARS=1):
            result = answer_question("What policy applies?")

        self.assertFalse(result["empty"])
        self.assertEqual(result["answer"], "Use the policy [1].")
        self.assertEqual(result["citations"], [citation])

    @override_settings(
        AI_RAG_CONVERSATION_MAX_TURNS=5,
        AI_RAG_CONVERSATION_MAX_CHARS=4000,
    )
    @patch("documents.rag.generate_rag_answer")
    @patch("documents.rag.retrieve_answer_context")
    def test_answer_question_runs_fresh_retrieval_for_follow_up(
        self,
        mock_context,
        mock_generate,
    ):
        citation = {
            "citation_id": 1,
            "document": Mock(),
            "chunk_text": "Automatic renewal applies.",
        }
        history = [{
            "question": "Which contracts expire soon?",
            "answer": "The Alpha agreement expires soon.",
        }]
        mock_context.return_value = [citation]
        mock_generate.return_value = "It renews automatically [1]."

        with self.settings(AI_RAG_MIN_CONTEXT_CHARS=1):
            answer_question(
                "Does it renew automatically?",
                documents_queryset=Mock(),
                conversation_history=history,
            )

        retrieval_query = mock_context.call_args.args[0]
        self.assertIn("Does it renew automatically?", retrieval_query)
        self.assertIn("Alpha agreement", retrieval_query)
        mock_context.assert_called_once()

    @patch("documents.rag.generate_rag_answer")
    @patch("documents.rag.retrieve_question_context")
    def test_answer_question_refuses_low_context_without_generation(
        self,
        mock_context,
        mock_generate,
    ):
        citation = {
            "citation_id": 1,
            "document": Mock(),
            "chunk_text": "Short",
        }
        mock_context.return_value = [citation]

        with self.settings(AI_RAG_MIN_CONTEXT_CHARS=20):
            result = answer_question("What policy applies?")

        self.assertTrue(result["empty"])
        self.assertEqual(result["answer"], LOW_CONTEXT_REFUSAL)
        self.assertEqual(result["citations"], [citation])
        mock_generate.assert_not_called()

    @patch("documents.rag.retrieve_question_context")
    def test_answer_question_handles_empty_retrieval(self, mock_context):
        mock_context.return_value = []

        result = answer_question("Unknown topic")

        self.assertTrue(result["empty"])
        self.assertEqual(result["citations"], [])
        self.assertIn("do not contain enough", result["answer"])

    @override_settings(AI_RAG_MIN_CONTEXT_CHARS=10)
    def test_has_sufficient_context_checks_combined_allowed_context(self):
        self.assertFalse(has_sufficient_context([
            {"chunk_text": "tiny"},
        ]))
        self.assertTrue(has_sufficient_context([
            {"chunk_text": "enough context here"},
        ]))

    @override_settings(MCP_RETRIEVAL_ENABLED=False)
    @patch("documents.rag.retrieve_question_context")
    def test_retrieve_answer_context_uses_direct_path_when_mcp_disabled(
        self,
        mock_direct,
    ):
        direct_context = [{"citation_id": 1}]
        accessible_documents = Mock()
        mock_direct.return_value = direct_context

        contexts = retrieve_answer_context(
            "What policy applies?",
            documents_queryset=accessible_documents,
        )

        self.assertEqual(contexts, direct_context)
        mock_direct.assert_called_once_with(
            "What policy applies?",
            documents_queryset=accessible_documents,
        )

    @override_settings(
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=True,
        AI_RAG_TOP_K=3,
        AI_RAG_MAX_CONTEXT_CHARS=1800,
    )
    @patch("documents.mcp_retrieval.search_documents")
    def test_retrieve_answer_context_uses_mcp_when_enabled(self, mock_mcp):
        document = Mock()
        document.id = 42
        document.file.name = "documents/policy.pdf"
        accessible_documents = Mock()
        accessible_documents.in_bulk.return_value = {42: document}
        mock_mcp.return_value = {
            "status": "ok",
            "results": [
                {
                    "citation_id": 1,
                    "document_id": 42,
                    "chunk_id": 99,
                    "chunk_index": 0,
                    "snippet": "Policy context",
                    "score": 1.4,
                }
            ],
        }

        contexts = retrieve_answer_context(
            "What policy applies?",
            documents_queryset=accessible_documents,
        )

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0]["document"], document)
        self.assertEqual(contexts[0]["chunk_text"], "Policy context")
        self.assertEqual(contexts[0]["score"], 1.4)
        payload = mock_mcp.call_args.args[0]
        self.assertEqual(payload["query"], "What policy applies?")
        self.assertEqual(payload["options"]["max_results"], 3)
        self.assertEqual(payload["trace"]["source"], "answer_question")
        self.assertTrue(payload["trace"]["request_id"])
        self.assertEqual(
            mock_mcp.call_args.kwargs["documents_queryset"],
            accessible_documents,
        )

    @override_settings(
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=True,
    )
    @patch("documents.rag.retrieve_question_context")
    @patch("documents.mcp_retrieval.search_documents")
    def test_retrieve_answer_context_falls_back_when_mcp_fails(
        self,
        mock_mcp,
        mock_direct,
    ):
        accessible_documents = Mock()
        fallback_context = [{"citation_id": 1, "chunk_text": "Direct context"}]
        mock_mcp.return_value = {
            "status": "error",
            "error": {"code": "retrieval_unavailable"},
        }
        mock_direct.return_value = fallback_context

        contexts = retrieve_answer_context(
            "What policy applies?",
            documents_queryset=accessible_documents,
        )

        self.assertEqual(contexts, fallback_context)
        mock_direct.assert_called_once_with(
            "What policy applies?",
            documents_queryset=accessible_documents,
        )

    @override_settings(
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=True,
    )
    @patch("documents.rag.retrieve_question_context")
    @patch("documents.mcp_retrieval.search_documents")
    def test_retrieve_answer_context_logs_mcp_fallback(
        self,
        mock_mcp,
        mock_direct,
    ):
        mock_mcp.return_value = {
            "status": "error",
            "error": {"code": "retrieval_unavailable", "retryable": True},
        }
        mock_direct.return_value = [{"citation_id": 1}]

        with self.assertLogs("documents.rag", level="WARNING") as logs:
            retrieve_answer_context("What policy applies?", documents_queryset=Mock())

        self.assertIn("path=fallback", "\n".join(logs.output))
        self.assertIn("MCP retrieval failed: retrieval_unavailable", "\n".join(
            logs.output
        ))

    @override_settings(
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=False,
    )
    @patch("documents.rag.retrieve_question_context")
    @patch("documents.mcp_retrieval.search_documents")
    def test_retrieve_answer_context_raises_without_fallback(
        self,
        mock_mcp,
        mock_direct,
    ):
        mock_mcp.return_value = {
            "status": "error",
            "error": {"code": "permission_denied"},
        }

        with self.assertRaisesMessage(
            RAGError,
            "MCP retrieval failed: permission_denied",
        ):
            retrieve_answer_context("What policy applies?", documents_queryset=Mock())

        mock_direct.assert_not_called()


class AskConversationTests(SimpleTestCase):
    def test_serialize_ask_turn_keeps_session_safe_citation_data(self):
        document = Mock()
        document.id = 42
        result = {
            "answer": "Grounded answer [1].",
            "citations": [{
                "document": document,
                "chunk_text": "A" * 600,
                "score": 1.25,
            }],
        }

        turn = serialize_ask_turn("What applies?", result)

        self.assertEqual(turn["citations"][0]["document_id"], 42)
        self.assertEqual(len(turn["citations"][0]["chunk_text"]), 500)
        self.assertNotIn("document", turn["citations"][0])

    def test_hydrate_conversation_removes_turn_with_inaccessible_citations(self):
        accessible_document = Mock()
        accessible_document.id = 7
        accessible_documents = Mock()
        accessible_documents.in_bulk.return_value = {7: accessible_document}
        stored_turns = [{
            "question": "Which contracts expire?",
            "answer": "Two contracts expire.",
            "citations": [
                {"document_id": 7, "chunk_text": "Allowed", "score": 1.2},
                {"document_id": 8, "chunk_text": "Denied", "score": 1.1},
            ],
        }]

        conversation = hydrate_ask_conversation(
            stored_turns,
            accessible_documents,
        )

        self.assertEqual(conversation, [])


class MCPRetrievalTests(SimpleTestCase):
    def build_document(self):
        document = Mock()
        document.file.name = "documents/policy.pdf"
        document.document_type = "Policy"
        document.document_subtype = "Benefits"
        document.department = "HR"
        document.author = "Admin"
        document.tags = "benefits, health"
        document.uploaded_at = SimpleNamespace(
            isoformat=Mock(return_value="2026-06-01T12:00:00+00:00")
        )
        return document

    @override_settings(
        AI_RAG_TOP_K=2,
        AI_RAG_MAX_CONTEXT_CHARS=20,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_returns_contract_response(self, mock_retrieve):
        document = self.build_document()
        mock_retrieve.return_value = [
            {
                "citation_id": 1,
                "document": document,
                "document_id": 42,
                "chunk_id": 99,
                "chunk_index": 0,
                "chunk_text": "Coverage starts after eligibility is verified.",
                "score": 1.7,
            }
        ]
        accessible_documents = Mock()

        with self.assertLogs("documents.mcp_retrieval", level="INFO") as logs:
            response = mcp_search_documents(
                {
                    "query": "When does coverage start?",
                    "user_context": {"roles": ["viewer"]},
                    "options": {"max_results": 1, "max_chunk_chars": 18},
                    "trace": {"request_id": "request-123"},
                },
                documents_queryset=accessible_documents,
            )

        self.assertEqual(response["status"], "ok")
        self.assertIn("mcp_retrieval status=ok", "\n".join(logs.output))
        self.assertIn("request_id=request-123", "\n".join(logs.output))
        self.assertEqual(response["query"], "When does coverage start?")
        self.assertEqual(response["trace"]["request_id"], "request-123")
        self.assertEqual(response["summary"]["returned_count"], 1)
        result = response["results"][0]
        self.assertEqual(result["citation_id"], 1)
        self.assertEqual(result["document_id"], 42)
        self.assertEqual(result["chunk_id"], 99)
        self.assertEqual(result["snippet"], "Coverage starts af")
        self.assertEqual(result["source"]["department"], "HR")
        self.assertEqual(result["source"]["tags"], ["benefits", "health"])
        self.assertEqual(result["source"]["open_url"], "/view/42/")
        self.assertEqual(
            result["retrieval"]["embedding_model"],
            "amazon.titan-embed-text-v2:0",
        )
        mock_retrieve.assert_called_once_with(
            "When does coverage start?",
            top_k=1,
            documents_queryset=accessible_documents,
        )

    def test_search_documents_rejects_empty_query(self):
        with self.assertLogs("documents.mcp_retrieval", level="INFO") as logs:
            response = mcp_search_documents({
                "query": " ",
                "trace": {"request_id": "empty-request"},
            })

        self.assertEqual(response["status"], "error")
        self.assertIn("code=invalid_request", "\n".join(logs.output))
        self.assertEqual(response["error"]["code"], "invalid_request")
        self.assertFalse(response["error"]["retryable"])
        self.assertEqual(response["trace"]["request_id"], "empty-request")

    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_returns_empty_contract_response(self, mock_retrieve):
        mock_retrieve.return_value = []

        response = mcp_search_documents(
            {"query": "unknown topic", "user_context": {"roles": ["viewer"]}},
            documents_queryset=Mock(),
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["results"], [])
        self.assertEqual(
            response["summary"]["empty_reason"],
            "no_accessible_context",
        )

    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_maps_embedding_errors(self, mock_retrieve):
        mock_retrieve.side_effect = EmbeddingError("AWS failed")

        response = mcp_search_documents(
            {"query": "policy", "user_context": {"roles": ["viewer"]}},
            documents_queryset=Mock(),
        )

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "embedding_provider_error")
        self.assertTrue(response["error"]["retryable"])

    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_maps_retrieval_errors(self, mock_retrieve):
        mock_retrieve.side_effect = RAGError("OpenSearch retrieval failed: down")

        response = mcp_search_documents(
            {"query": "policy", "user_context": {"roles": ["viewer"]}},
            documents_queryset=Mock(),
        )

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "retrieval_unavailable")
        self.assertTrue(response["error"]["retryable"])

    @override_settings(
        OKTA_GROUP_VIEWER="DjangoViewer",
        OKTA_GROUP_LOADER="DjangoLoader",
        OKTA_GROUP_ADMIN="DjangoAdmin",
    )
    @patch("documents.mcp_retrieval.Document")
    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_derives_accessible_documents_from_user_context(
        self,
        mock_retrieve,
        mock_document,
    ):
        queryset = Mock()
        mock_document.objects.all.return_value = queryset
        mock_retrieve.return_value = []

        mcp_search_documents({
            "query": "policy",
            "user_context": {"groups": ["DjangoViewer"]},
        })

        mock_document.objects.all.assert_called_once()
        mock_retrieve.assert_called_once_with(
            "policy",
            top_k=5,
            documents_queryset=queryset,
        )

    @override_settings(
        OKTA_GROUP_VIEWER="DjangoViewer",
        OKTA_GROUP_LOADER="DjangoLoader",
        OKTA_GROUP_ADMIN="DjangoAdmin",
    )
    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_denies_unknown_roles(
        self,
        mock_retrieve,
    ):
        response = mcp_search_documents({
            "query": "policy",
            "user_context": {"groups": ["UnknownGroup"]},
        })

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "permission_denied")
        self.assertFalse(response["error"]["retryable"])
        mock_retrieve.assert_not_called()

    @patch("documents.mcp_retrieval.retrieve_question_context")
    def test_search_documents_requires_permission_context(self, mock_retrieve):
        response = mcp_search_documents({"query": "policy"})

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "permission_context_missing")
        self.assertFalse(response["error"]["retryable"])
        mock_retrieve.assert_not_called()


class MCPIndexingTests(SimpleTestCase):
    def build_document(self, extracted_text="First paragraph.\n\nSecond paragraph."):
        document = Mock(
            id=42,
            extracted_text=extracted_text,
        )
        document.chunks.count.return_value = 2
        return document

    @override_settings(
        AI_EMBEDDING_MAX_CHARS=2500,
        AI_EMBEDDING_DIMENSIONS=1024,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_DOCUMENT_INDEX="docmanager-documents",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.mcp_indexing.reindex_document")
    @patch("documents.mcp_indexing.rebuild_document_embeddings")
    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_returns_contract_response(
        self,
        mock_get,
        mock_rebuild,
        mock_reindex,
    ):
        document = self.build_document("Secret policy text")
        mock_get.return_value = document
        mock_rebuild.return_value = 3
        mock_reindex.return_value = 3

        with self.assertLogs("documents.mcp_indexing", level="INFO") as logs:
            response = mcp_index_document({
                "document": {
                    "document_id": 42,
                    "content_version": "sha256:test",
                },
                "trace": {"request_id": "index-request-123"},
            })

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["document_id"], 42)
        self.assertEqual(response["content_version"], "sha256:test")
        self.assertEqual(response["trace"]["request_id"], "index-request-123")
        self.assertEqual(response["summary"]["chunks_created"], 3)
        self.assertEqual(response["summary"]["chunks_replaced"], 2)
        self.assertEqual(response["summary"]["chunks_embedded"], 3)
        self.assertEqual(response["summary"]["document_records_indexed"], 1)
        self.assertEqual(response["summary"]["chunk_records_indexed"], 3)
        self.assertFalse(response["summary"]["dry_run"])
        self.assertEqual(
            response["indexing"]["embedding_model"],
            "amazon.titan-embed-text-v2:0",
        )
        self.assertEqual(
            response["indexing"]["chunk_index"],
            "docmanager-document-chunks",
        )
        mock_get.assert_called_once_with(pk=42)
        mock_rebuild.assert_called_once_with(document)
        mock_reindex.assert_called_once_with(document, create_indexes=True)
        self.assertIn("mcp_indexing status=ok", "\n".join(logs.output))
        self.assertIn("request_id=index-request-123", "\n".join(logs.output))
        self.assertNotIn("Secret policy text", "\n".join(logs.output))

    @override_settings(
        AI_EMBEDDING_MAX_CHARS=20,
        AI_EMBEDDING_DIMENSIONS=1024,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
    )
    @patch("documents.mcp_indexing.reindex_document")
    @patch("documents.mcp_indexing.rebuild_document_embeddings")
    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_dry_run_does_not_write(
        self,
        mock_get,
        mock_rebuild,
        mock_reindex,
    ):
        document = self.build_document("First paragraph.\n\nSecond paragraph.")
        mock_get.return_value = document

        response = mcp_index_document({
            "document": {"document_id": 42},
            "options": {"dry_run": True, "max_chunk_chars": 20},
            "trace": {"request_id": "dry-run"},
        })

        self.assertEqual(response["status"], "ok")
        self.assertTrue(response["summary"]["dry_run"])
        self.assertEqual(response["summary"]["chunks_created"], 2)
        self.assertEqual(response["summary"]["chunks_replaced"], 2)
        self.assertEqual(response["summary"]["chunks_embedded"], 0)
        self.assertEqual(response["summary"]["chunk_records_indexed"], 0)
        self.assertEqual(response["warnings"], ["dry_run_no_writes"])
        mock_rebuild.assert_not_called()
        mock_reindex.assert_not_called()

    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_rejects_missing_document_id(self, mock_get):
        response = mcp_index_document({
            "document": {},
            "trace": {"request_id": "missing-id"},
        })

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "invalid_request")
        self.assertFalse(response["error"]["retryable"])
        self.assertEqual(response["trace"]["request_id"], "missing-id")
        mock_get.assert_not_called()

    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_rejects_unsupported_options(self, mock_get):
        response = mcp_index_document({
            "document": {"document_id": 42},
            "options": {
                "chunking_strategy": "sentence",
            },
            "trace": {"request_id": "bad-options"},
        })

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["document_id"], 42)
        self.assertEqual(response["error"]["code"], "invalid_request")
        self.assertFalse(response["error"]["retryable"])
        mock_get.assert_not_called()

    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_maps_document_not_found(self, mock_get):
        mock_get.side_effect = Document.DoesNotExist

        response = mcp_index_document({
            "document": {"document_id": 404},
            "trace": {"request_id": "not-found"},
        })

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["document_id"], 404)
        self.assertEqual(response["error"]["code"], "document_not_found")
        self.assertFalse(response["error"]["retryable"])

    @patch("documents.mcp_indexing.rebuild_document_embeddings")
    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_rejects_empty_extracted_text(
        self,
        mock_get,
        mock_rebuild,
    ):
        mock_get.return_value = self.build_document("")

        response = mcp_index_document({"document": {"document_id": 42}})

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "no_extracted_text")
        self.assertFalse(response["error"]["retryable"])
        mock_rebuild.assert_not_called()

    @patch("documents.mcp_indexing.reindex_document")
    @patch("documents.mcp_indexing.rebuild_document_embeddings")
    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_can_reindex_metadata_without_extracted_text(
        self,
        mock_get,
        mock_rebuild,
        mock_reindex,
    ):
        mock_get.return_value = self.build_document("")
        mock_reindex.return_value = 0

        response = mcp_index_document({
            "document": {"document_id": 42},
            "options": {"replace_existing_chunks": False},
        })

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["summary"]["chunks_created"], 2)
        self.assertEqual(response["summary"]["chunks_embedded"], 2)
        self.assertEqual(response["summary"]["document_records_indexed"], 1)
        self.assertEqual(response["summary"]["chunk_records_indexed"], 0)
        mock_rebuild.assert_not_called()
        mock_reindex.assert_called_once()

    @patch("documents.mcp_indexing.reindex_document")
    @patch("documents.mcp_indexing.rebuild_document_embeddings")
    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_maps_embedding_errors(
        self,
        mock_get,
        mock_rebuild,
        mock_reindex,
    ):
        mock_get.return_value = self.build_document()
        mock_rebuild.side_effect = EmbeddingError("AWS failed")

        response = mcp_index_document({"document": {"document_id": 42}})

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "embedding_provider_error")
        self.assertTrue(response["error"]["retryable"])
        mock_reindex.assert_not_called()

    @patch("documents.mcp_indexing.reindex_document")
    @patch("documents.mcp_indexing.rebuild_document_embeddings")
    @patch("documents.mcp_indexing.Document.objects.get")
    def test_index_document_maps_opensearch_errors(
        self,
        mock_get,
        mock_rebuild,
        mock_reindex,
    ):
        mock_get.return_value = self.build_document()
        mock_rebuild.return_value = 2
        mock_reindex.side_effect = OpenSearchIndexingError("timeout")

        response = mcp_index_document({"document": {"document_id": 42}})

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "indexing_timeout")
        self.assertTrue(response["error"]["retryable"])


class MCPRetrievalHealthCommandTests(SimpleTestCase):
    @override_settings(
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=False,
        AI_RAG_TOP_K=5,
        AI_RAG_MAX_CONTEXT_CHARS=1800,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.management.commands.health_mcp_retrieval.search_documents")
    def test_health_mcp_retrieval_reports_ok_live_check(self, mock_search):
        mock_search.return_value = {
            "status": "ok",
            "summary": {
                "returned_count": 1,
                "candidate_count": 1,
                "empty_reason": "",
            },
        }
        stdout = StringIO()

        call_command("health_mcp_retrieval", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("mcp_settings=ok", output)
        self.assertIn("mcp_live=ok status=ok returned_count=1", output)
        payload = mock_search.call_args.args[0]
        self.assertEqual(payload["user_context"]["roles"], ["viewer"])
        self.assertEqual(payload["trace"]["source"], "health_mcp_retrieval")

    @override_settings(
        MCP_RETRIEVAL_ENABLED=False,
        MCP_RETRIEVAL_FALLBACK_ENABLED=True,
        AI_RAG_TOP_K=5,
        AI_RAG_MAX_CONTEXT_CHARS=1800,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    def test_health_mcp_retrieval_skip_live_reports_settings_warning(self):
        stdout = StringIO()

        call_command("health_mcp_retrieval", "--skip-live", stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("mcp_settings=warn enabled=False", output)
        self.assertIn("mcp_live=warn skipped live retrieval check", output)

    @override_settings(
        MCP_RETRIEVAL_ENABLED=True,
        MCP_RETRIEVAL_FALLBACK_ENABLED=False,
        AI_RAG_TOP_K=5,
        AI_RAG_MAX_CONTEXT_CHARS=1800,
        BEDROCK_EMBED_MODEL_ID="amazon.titan-embed-text-v2:0",
        OPENSEARCH_CHUNK_INDEX="docmanager-document-chunks",
    )
    @patch("documents.management.commands.health_mcp_retrieval.search_documents")
    def test_health_mcp_retrieval_raises_on_error_response(self, mock_search):
        mock_search.return_value = {
            "status": "error",
            "error": {
                "code": "retrieval_unavailable",
                "retryable": True,
            },
        }

        with self.assertRaises(CommandError):
            call_command("health_mcp_retrieval", stdout=StringIO())
