from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

import os

from .models import Document


def validate_uploaded_file(uploaded_file, allowed_extensions):
    if not uploaded_file:
        return

    extension = os.path.splitext(uploaded_file.name)[1].lower()

    if extension not in allowed_extensions:
        allowed = ", ".join(allowed_extensions)
        raise ValidationError(
            f"Unsupported file type '{extension}'. Allowed types: {allowed}."
        )

    allowed_content_types = settings.ALLOWED_UPLOAD_CONTENT_TYPES.get(
        extension,
        set()
    )
    content_type = getattr(uploaded_file, "content_type", "")

    if allowed_content_types and content_type not in allowed_content_types:
        allowed = ", ".join(sorted(allowed_content_types))
        raise ValidationError(
            f"Unsupported file content type '{content_type}'. "
            f"Allowed content types for {extension}: {allowed}."
        )

    if uploaded_file.size > settings.MAX_UPLOAD_SIZE_BYTES:
        raise ValidationError(
            f"File is too large. Maximum size is {settings.MAX_UPLOAD_SIZE_MB} MB."
        )


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = [
            "document_type",
            "document_subtype",
            "ocr_language",
            "department",
            "author",
            "description",
            "tags",
            "file",
        ]

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")
        validate_uploaded_file(uploaded_file, settings.ALLOWED_DOCUMENT_EXTENSIONS)
        return uploaded_file


class DocumentMetadataForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = [
            "document_type",
            "document_subtype",
            "ocr_language",
            "department",
            "author",
            "description",
            "tags",
        ]
