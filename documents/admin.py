# Register your models here.
from django.contrib import admin
from .models import AuditEvent, Document, DocumentChunk, MetadataQualityReview


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "document_type",
        "department",
        "author",
        "ai_suggestion_status",
        "uploaded_at",
    )
    search_fields = (
        "document_type",
        "department",
        "author",
        "tags",
        "ai_document_type",
        "ai_department",
        "ai_tags",
    )
    list_filter = (
        "document_type",
        "department",
        "ai_suggestion_status",
        "uploaded_at",
    )


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "action",
        "document_name",
        "actor_name",
        "actor_email",
        "created_at",
    )
    list_filter = ("action", "created_at")
    search_fields = ("document_name", "actor_name", "actor_email")
    readonly_fields = (
        "document",
        "document_name",
        "action",
        "actor_name",
        "actor_email",
        "metadata",
        "created_at",
    )


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "chunk_index",
        "embedding_model",
        "created_at",
    )
    search_fields = (
        "chunk_text",
        "document__file",
    )
    readonly_fields = (
        "document",
        "chunk_index",
        "chunk_text",
        "embedding",
        "embedding_model",
        "created_at",
    )


@admin.register(MetadataQualityReview)
class MetadataQualityReviewAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "quality_score",
        "quality_level",
        "status",
        "model_id",
        "reviewed_at",
    )
    list_filter = ("status", "quality_level", "reviewed_at")
    search_fields = ("document__file", "review_summary", "error")
    readonly_fields = (
        "document",
        "status",
        "quality_score",
        "quality_level",
        "review_summary",
        "issues",
        "model_id",
        "error",
        "reviewed_at",
    )
