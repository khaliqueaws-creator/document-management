# Register your models here.
from django.contrib import admin
from .models import AuditEvent, Document, DocumentChunk


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
        "embedding_vector",
        "embedding_model",
        "created_at",
    )
