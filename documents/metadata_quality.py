import json
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class MetadataQualityError(Exception):
    pass


ALLOWED_FIELDS = {
    "document_type",
    "document_subtype",
    "department",
    "author",
    "description",
    "tags",
}
FIELD_LABELS = {
    "document_type": "Document Type",
    "document_subtype": "Document Subtype",
    "department": "Department",
    "author": "Author",
    "description": "Description",
    "tags": "Tags",
}
ALLOWED_SEVERITIES = {"high", "medium", "low"}


def build_metadata_quality_prompt(document, text):
    current_metadata = {
        "document_type": document.document_type,
        "document_subtype": document.document_subtype,
        "department": document.department,
        "author": document.author,
        "description": document.description,
        "tags": document.tags,
    }
    return f"""
You are an enterprise document metadata quality reviewer.

Compare the current metadata with the document text. Evaluate whether the
metadata is accurate, relevant, and useful, not merely whether fields are
populated.

Return only valid JSON in this shape:
{{
  "quality_score": 0,
  "summary": "",
  "issues": [
    {{
      "field": "document_type|document_subtype|department|author|description|tags",
      "current_value": "",
      "suggested_value": "",
      "severity": "high|medium|low",
      "reason": "",
      "evidence": ""
    }}
  ]
}}

Rules:
- quality_score must be an integer from 0 to 100.
- A high score means the current metadata accurately represents the document.
- Identify populated values that conflict with the document meaning.
- Identify important missing or weak metadata.
- Return no issue when a field is already accurate and useful.
- suggested_value must be concise and suitable for the field.
- reason must explain the quality problem.
- evidence must be a short verbatim excerpt from the supplied document text.
- Do not invent evidence. Use an empty evidence value when no direct excerpt is available.
- Return no more than 8 issues.

Current metadata:
{json.dumps(current_metadata, ensure_ascii=True)}

Document text:
{text}
""".strip()


def load_json_object(response_text):
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


def clean_text(value, max_length):
    value = str(value or "").strip()
    return value[:max_length].rstrip()


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def quality_level_for_score(score):
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 50:
        return "needs_review"
    return "critical"


def parse_metadata_quality_response(response_text, source_text):
    response_text = (response_text or "").strip()
    if not response_text:
        raise MetadataQualityError("Bedrock returned an empty quality review.")

    try:
        parsed = load_json_object(response_text)
    except json.JSONDecodeError as error:
        raise MetadataQualityError(
            "Bedrock returned invalid quality review JSON."
        ) from error

    if not isinstance(parsed, dict):
        raise MetadataQualityError(
            "Bedrock quality review was not a JSON object."
        )

    try:
        score = int(parsed.get("quality_score"))
    except (TypeError, ValueError) as error:
        raise MetadataQualityError(
            "Bedrock quality review did not include a valid score."
        ) from error

    if score < 0 or score > 100:
        raise MetadataQualityError(
            "Bedrock quality score must be between 0 and 100."
        )

    raw_issues = parsed.get("issues") or []
    if not isinstance(raw_issues, list):
        raise MetadataQualityError(
            "Bedrock quality review issues must be a list."
        )

    normalized_source = normalize_text(source_text)
    issues = []

    for raw_issue in raw_issues[:8]:
        if not isinstance(raw_issue, dict):
            continue

        field = clean_text(raw_issue.get("field"), 50).lower()
        severity = clean_text(raw_issue.get("severity"), 20).lower()
        if field not in ALLOWED_FIELDS or severity not in ALLOWED_SEVERITIES:
            continue

        evidence = clean_text(raw_issue.get("evidence"), 500)
        if evidence and normalize_text(evidence) not in normalized_source:
            evidence = ""

        issues.append({
            "field": field,
            "field_label": FIELD_LABELS[field],
            "current_value": clean_text(raw_issue.get("current_value"), 500),
            "suggested_value": clean_text(
                raw_issue.get("suggested_value"),
                500,
            ),
            "severity": severity,
            "reason": clean_text(raw_issue.get("reason"), 800),
            "evidence": evidence,
        })

    return {
        "quality_score": score,
        "quality_level": quality_level_for_score(score),
        "summary": clean_text(parsed.get("summary"), 1000),
        "issues": issues,
    }


def parse_bedrock_response(payload):
    try:
        content = payload["output"]["message"]["content"]
        response_text = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    except (KeyError, TypeError) as error:
        raise MetadataQualityError(
            "Bedrock returned an invalid quality review response."
        ) from error

    if not response_text:
        raise MetadataQualityError(
            "Bedrock returned an empty quality review response."
        )
    return response_text


def review_document_metadata(document):
    source_text = (document.extracted_text or "").strip()
    if not source_text or source_text.startswith("TEXT_EXTRACTION_FAILED:"):
        raise MetadataQualityError(
            "No extracted text is available for metadata quality review."
        )

    review_text = source_text[:settings.AI_METADATA_MAX_CHARS]
    prompt = build_metadata_quality_prompt(document, review_text)

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
            body=json.dumps({
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    },
                ],
                "inferenceConfig": {
                    "temperature": 0.1,
                    "maxTokens": 1400,
                },
            }),
        )
    except (BotoCoreError, ClientError) as error:
        raise MetadataQualityError(
            f"Unable to reach Bedrock: {error}"
        ) from error

    response_body = response.get("body")
    if response_body is None:
        raise MetadataQualityError(
            "Bedrock returned an invalid quality review response."
        )

    try:
        payload = json.loads(response_body.read())
    except (AttributeError, TypeError, ValueError) as error:
        raise MetadataQualityError(
            "Bedrock returned a non-JSON quality review response."
        ) from error

    return parse_metadata_quality_response(
        parse_bedrock_response(payload),
        review_text,
    )
