import json

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class MetadataSuggestionError(Exception):
    pass


def build_metadata_prompt(text):
    return f"""
You are a document metadata extraction assistant.

Extract metadata from the document text.

Return only valid JSON with these fields:
{{
  "document_type": "",
  "department": "",
  "tags": "",
  "summary": ""
}}

Rules:
- document_type should be short, like Invoice, Contract, Policy, Resume, Report, Letter, Other.
- department should be short, like Finance, HR, Legal, Operations, IT, Other.
- tags should be comma-separated and contain 3 to 8 useful tags.
- summary should be one or two sentences.
- Do not invent facts not supported by the text.
- If the text is unclear, use "Other" for document_type or department.

Document text:
{text}
""".strip()


def clean_suggestion_value(value, max_length=None):
    if value is None:
        return ""

    value = str(value).strip()

    if max_length and len(value) > max_length:
        return value[:max_length].rstrip()

    return value


def load_metadata_json_object(response_text):
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()

        for index, character in enumerate(response_text):
            if character != "{":
                continue

            try:
                parsed, _ = decoder.raw_decode(response_text[index:])
            except json.JSONDecodeError:
                continue

            return parsed

        raise


def parse_metadata_json_response(response_text):
    response_text = (response_text or "").strip()

    if not response_text:
        raise MetadataSuggestionError("AI provider returned an empty response.")

    try:
        parsed = load_metadata_json_object(response_text)
    except json.JSONDecodeError as error:
        raise MetadataSuggestionError(
            "AI provider returned invalid JSON."
        ) from error

    if not isinstance(parsed, dict):
        raise MetadataSuggestionError(
            "AI provider response was not a JSON object."
        )

    return {
        "document_type": clean_suggestion_value(
            parsed.get("document_type"),
            100,
        ),
        "department": clean_suggestion_value(
            parsed.get("department"),
            100,
        ),
        "tags": clean_suggestion_value(
            parsed.get("tags"),
            255,
        ),
        "summary": clean_suggestion_value(parsed.get("summary")),
    }


def build_provider_error_message(provider_name, response):
    response_text = response.text.strip()

    if len(response_text) > 500:
        response_text = response_text[:500].rstrip()

    if response_text:
        return (
            f"{provider_name} returned HTTP {response.status_code}: "
            f"{response_text}"
        )

    return f"{provider_name} returned HTTP {response.status_code}."


def parse_gemini_response_text(payload):
    candidates = payload.get("candidates") or []

    if not candidates:
        raise MetadataSuggestionError("Gemini returned no candidates.")

    parts = (
        candidates[0]
        .get("content", {})
        .get("parts", [])
    )

    response_text = "".join(
        part.get("text", "")
        for part in parts
        if isinstance(part, dict)
    ).strip()

    if not response_text:
        raise MetadataSuggestionError("Gemini returned an empty response.")

    return response_text


def parse_bedrock_response_text(payload):
    if not isinstance(payload, dict):
        raise MetadataSuggestionError("Bedrock returned an invalid response.")

    content = (
        payload.get("output", {})
        .get("message", {})
        .get("content", [])
    )

    if not isinstance(content, list):
        raise MetadataSuggestionError("Bedrock returned an invalid response.")

    response_text = "".join(
        item.get("text", "")
        for item in content
        if isinstance(item, dict)
    ).strip()

    if not response_text:
        raise MetadataSuggestionError("Bedrock returned an empty response.")

    return response_text


def suggest_metadata_with_ollama(text):
    text = (text or "").strip()

    if not text:
        raise MetadataSuggestionError(
            "No extracted text is available for metadata suggestions."
        )

    prompt = build_metadata_prompt(text[:settings.AI_METADATA_MAX_CHARS])

    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate",
            json={
                "model": settings.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "num_ctx": settings.OLLAMA_NUM_CTX,
                },
            },
            timeout=settings.OLLAMA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise MetadataSuggestionError(
            f"Unable to reach Ollama: {error}"
        ) from error

    if response.status_code >= 400:
        raise MetadataSuggestionError(
            build_provider_error_message("Ollama", response)
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise MetadataSuggestionError(
            "Ollama returned a non-JSON API response."
        ) from error

    response_text = payload.get("response", "")

    return parse_metadata_json_response(response_text)


def suggest_metadata_with_gemini(text):
    text = (text or "").strip()

    if not text:
        raise MetadataSuggestionError(
            "No extracted text is available for metadata suggestions."
        )

    if not settings.GEMINI_API_KEY:
        raise MetadataSuggestionError("GEMINI_API_KEY is not configured.")

    prompt = build_metadata_prompt(text[:settings.AI_METADATA_MAX_CHARS])
    model = settings.GEMINI_MODEL
    url = (
        f"{settings.GEMINI_BASE_URL.rstrip('/')}/v1beta/models/"
        f"{model}:generateContent"
    )

    try:
        response = requests.post(
            url,
            params={"key": settings.GEMINI_API_KEY},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                        ],
                    },
                ],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.2,
                },
            },
            timeout=settings.OLLAMA_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise MetadataSuggestionError(
            f"Unable to reach Gemini: {error}"
        ) from error

    if response.status_code >= 400:
        raise MetadataSuggestionError(
            build_provider_error_message("Gemini", response)
        )

    try:
        payload = response.json()
    except ValueError as error:
        raise MetadataSuggestionError(
            "Gemini returned a non-JSON API response."
        ) from error

    return parse_metadata_json_response(parse_gemini_response_text(payload))


def suggest_metadata_with_bedrock(text):
    text = (text or "").strip()

    if not text:
        raise MetadataSuggestionError(
            "No extracted text is available for metadata suggestions."
        )

    prompt = build_metadata_prompt(text[:settings.AI_METADATA_MAX_CHARS])

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
            body=json.dumps(
                {
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
                        "maxTokens": 1000,
                    },
                }
            ),
        )
    except (BotoCoreError, ClientError) as error:
        raise MetadataSuggestionError(
            f"Unable to reach Bedrock: {error}"
        ) from error

    response_body = response.get("body")

    if response_body is None:
        raise MetadataSuggestionError("Bedrock returned an invalid response.")

    try:
        payload = json.loads(response_body.read())
    except (AttributeError, TypeError, ValueError) as error:
        raise MetadataSuggestionError(
            "Bedrock returned a non-JSON API response."
        ) from error

    return parse_metadata_json_response(parse_bedrock_response_text(payload))


def suggest_metadata(text):
    provider = settings.AI_METADATA_PROVIDER

    if provider == "ollama":
        return suggest_metadata_with_ollama(text)

    if provider == "gemini":
        return suggest_metadata_with_gemini(text)

    if provider == "bedrock":
        return suggest_metadata_with_bedrock(text)

    raise MetadataSuggestionError(
        f"Unsupported AI metadata provider: {provider}"
    )
