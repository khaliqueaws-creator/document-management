from django import forms
from .models import Document


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = [
            "document_type",
            "document_subtype",
            "department",
            "author",
            "description",
            "tags",
            "file",
        ]