from django.db import models


class Document(models.Model):
    OCR_LANGUAGE_ENGLISH = "english"
    OCR_LANGUAGE_HINDI = "hindi"
    OCR_LANGUAGE_URDU = "urdu"

    OCR_LANGUAGE_CHOICES = [
        (OCR_LANGUAGE_ENGLISH, "English"),
        (OCR_LANGUAGE_HINDI, "Hindi"),
        (OCR_LANGUAGE_URDU, "Urdu"),
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

    def __str__(self):
        return self.file.name


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
