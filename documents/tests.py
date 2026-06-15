import json
from io import BytesIO, StringIO
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
)
from .embeddings import (
    EmbeddingError,
    chunk_text,
    get_titan_embedding,
    rebuild_document_embeddings,
)
from .mcp_retrieval import search_documents as mcp_search_documents
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
    build_rag_prompt,
    generate_rag_answer,
    has_sufficient_context,
    parse_bedrock_rag_response,
    retrieve_question_context,
)
from .semantic_search import cosine_similarity, search_documents_by_meaning
from .views import try_rebuild_document_embeddings, try_reindex_document
from .views import accept_ai_metadata, reject_ai_metadata


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
    @patch("documents.views.Document.objects.get")
    def test_accept_ai_metadata_saves_postgres_before_reindexing(
        self,
        mock_get,
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
        mock_reindex.assert_called_once_with(request, document)
        mock_audit.assert_called_once()
        mock_success.assert_called_once()

    @override_settings(
        OKTA_GROUP_VIEWER="DocumentViewer",
        OKTA_GROUP_LOADER="DocumentLoader",
        OKTA_GROUP_ADMIN="DocumentAdmin",
    )
    @patch("documents.views.messages.info")
    @patch("documents.views.try_reindex_document")
    @patch("documents.views.Document.objects.get")
    def test_reject_ai_metadata_saves_postgres_before_reindexing(
        self,
        mock_get,
        mock_reindex,
        mock_info,
    ):
        request = self.get_loader_request()
        document = Mock(id=42)
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
        response = mcp_search_documents({
            "query": " ",
            "trace": {"request_id": "empty-request"},
        })

        self.assertEqual(response["status"], "error")
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
