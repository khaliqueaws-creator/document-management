from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .ai_metadata import (
    MetadataSuggestionError,
    parse_metadata_json_response,
    suggest_metadata,
    suggest_metadata_with_gemini,
    suggest_metadata_with_ollama,
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

    @override_settings(AI_METADATA_PROVIDER="unknown")
    def test_suggest_metadata_rejects_unknown_provider(self):
        with self.assertRaisesMessage(
            MetadataSuggestionError,
            "Unsupported AI metadata provider",
        ):
            suggest_metadata("Some document text")
