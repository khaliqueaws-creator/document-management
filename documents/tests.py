import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .ai_metadata import (
    MetadataSuggestionError,
    parse_metadata_json_response,
    suggest_metadata,
    suggest_metadata_with_bedrock,
    suggest_metadata_with_gemini,
    suggest_metadata_with_ollama,
)
from .embeddings import (
    EmbeddingError,
    chunk_text,
    get_titan_embedding,
    rebuild_document_embeddings,
)
from .semantic_search import cosine_similarity, search_documents_by_meaning
from .views import try_rebuild_document_embeddings


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


class SemanticSearchTests(SimpleTestCase):
    def test_cosine_similarity_scores_matching_vectors(self):
        self.assertEqual(cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_cosine_similarity_handles_invalid_vectors(self):
        self.assertEqual(cosine_similarity([], [1, 0]), 0.0)
        self.assertEqual(cosine_similarity([1, 0], [1]), 0.0)
        self.assertEqual(cosine_similarity(["bad"], [1]), 0.0)

    @patch("documents.semantic_search.get_titan_embedding")
    @patch("documents.semantic_search.DocumentChunk.objects.select_related")
    def test_search_documents_by_meaning_returns_empty_without_embeddings(
        self,
        mock_select_related,
        mock_embedding,
    ):
        mock_embedding.return_value = [1, 0]
        mock_select_related.return_value.exclude.return_value = []

        self.assertEqual(search_documents_by_meaning("employee onboarding"), [])

    @override_settings(AI_SEARCH_TOP_K=5)
    @patch("documents.semantic_search.get_titan_embedding")
    @patch("documents.semantic_search.DocumentChunk.objects.select_related")
    def test_search_documents_by_meaning_ranks_best_document(
        self,
        mock_select_related,
        mock_embedding,
    ):
        mock_embedding.return_value = [1, 0]
        onboarding_document = Mock()
        finance_document = Mock()
        mock_select_related.return_value.exclude.return_value = [
            SimpleNamespace(
                document_id=1,
                document=onboarding_document,
                chunk_text="Employee onboarding checklist",
                embedding=[0.9, 0.1],
            ),
            SimpleNamespace(
                document_id=2,
                document=finance_document,
                chunk_text="Vendor payment invoice",
                embedding=[0, 1],
            ),
            SimpleNamespace(
                document_id=1,
                document=onboarding_document,
                chunk_text="Employee orientation benefits",
                embedding=[1, 0],
            ),
        ]

        results = search_documents_by_meaning("employee onboarding")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["document"], onboarding_document)
        self.assertEqual(
            results[0]["best_chunk"],
            "Employee orientation benefits",
        )

    @override_settings(AI_SEARCH_TOP_K=1)
    @patch("documents.semantic_search.get_titan_embedding")
    @patch("documents.semantic_search.DocumentChunk.objects.select_related")
    def test_search_documents_by_meaning_limits_results(
        self,
        mock_select_related,
        mock_embedding,
    ):
        mock_embedding.return_value = [1, 0]
        first_document = Mock()
        second_document = Mock()
        mock_select_related.return_value.exclude.return_value = [
            SimpleNamespace(
                document_id=1,
                document=first_document,
                chunk_text="Best match",
                embedding=[1, 0],
            ),
            SimpleNamespace(
                document_id=2,
                document=second_document,
                chunk_text="Second match",
                embedding=[0.8, 0.2],
            ),
        ]

        results = search_documents_by_meaning("employee onboarding")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["document"], first_document)
