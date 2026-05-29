from django.db import models
from pgvector.django import VectorField


class Document(models.Model):
    OCR_LANGUAGE_ENGLISH = "english"
    OCR_LANGUAGE_HINDI = "hindi"
    OCR_LANGUAGE_URDU = "urdu"
    AI_STATUS_NOT_REQUESTED = "not_requested"
    AI_STATUS_PENDING = "pending"
    AI_STATUS_SUGGESTED = "suggested"
    AI_STATUS_ACCEPTED = "accepted"
    AI_STATUS_REJECTED = "rejected"
    AI_STATUS_FAILED = "failed"
    AI_PROVIDER_OLLAMA = "ollama"
    AI_PROVIDER_GEMINI = "gemini"
    AI_PROVIDER_BEDROCK = "bedrock"

    OCR_LANGUAGE_CHOICES = [
        (OCR_LANGUAGE_ENGLISH, "English"),
        (OCR_LANGUAGE_HINDI, "Hindi"),
        (OCR_LANGUAGE_URDU, "Urdu"),
    ]
    AI_SUGGESTION_STATUS_CHOICES = [
        (AI_STATUS_NOT_REQUESTED, "Not requested"),
        (AI_STATUS_PENDING, "Pending"),
        (AI_STATUS_SUGGESTED, "Suggested"),
        (AI_STATUS_ACCEPTED, "Accepted"),
        (AI_STATUS_REJECTED, "Rejected"),
        (AI_STATUS_FAILED, "Failed"),
    ]
    AI_METADATA_PROVIDER_CHOICES = [
        (AI_PROVIDER_OLLAMA, "Ollama"),
        (AI_PROVIDER_GEMINI, "Gemini"),
        (AI_PROVIDER_BEDROCK, "AWS Bedrock Nova Lite"),
    ]

    document_type = models.CharField(max_length=100, blank=True)
    document_subtype = models.CharField(max_length=100,blank=True)
    ocr_language = models.CharField(
        max_length=20,
        choices=OCR_LANGUAGE_CHOICES,
        default=OCR_LANGUAGE_ENGLISH,
    )
    ocr_text = models.TextField(blank=True)	
    department = models.CharField(max_length=100)
    author = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    tags = models.CharField(max_length=255, blank=True)

    file = models.FileField(upload_to="documents/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    extracted_text = models.TextField(blank=True, null=True)
    ai_document_type = models.CharField(max_length=100, blank=True)
    ai_department = models.CharField(max_length=100, blank=True)
    ai_tags = models.CharField(max_length=255, blank=True)
    ai_summary = models.TextField(blank=True)
    ai_metadata_provider = models.CharField(
        max_length=20,
        choices=AI_METADATA_PROVIDER_CHOICES,
        blank=True,
    )
    ai_suggestion_status = models.CharField(
        max_length=20,
        choices=AI_SUGGESTION_STATUS_CHOICES,
        default=AI_STATUS_NOT_REQUESTED,
    )
    ai_suggested_at = models.DateTimeField(null=True, blank=True)
    ai_error = models.TextField(blank=True)

    def __str__(self):
        return self.file.name


class DocumentChunk(models.Model):
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    chunk_text = models.TextField()
    embedding = models.JSONField(default=list, blank=True)
    embedding_vector = VectorField(
        dimensions=1024,
        null=True,
        blank=True,
    )
    embedding_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "chunk_index"]
        unique_together = ("document", "chunk_index")

    def __str__(self):
        return f"{self.document_id} - chunk {self.chunk_index}"


class AuditEvent(models.Model):
    ACTION_UPLOAD = "upload"
    ACTION_EDIT = "edit"
    ACTION_DELETE = "delete"

    ACTION_CHOICES = [
        (ACTION_UPLOAD, "Upload"),
        (ACTION_EDIT, "Edit metadata"),
        (ACTION_DELETE, "Delete"),
    ]

    document = models.ForeignKey(
        Document,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    document_name = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    actor_name = models.CharField(max_length=150, blank=True)
    actor_email = models.EmailField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} - {self.document_name}"
