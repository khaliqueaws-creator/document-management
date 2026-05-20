# Register your models here.
from django.contrib import admin
from .models import AuditEvent, Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("document_type", "department", "author", "uploaded_at")
    search_fields = ("document_type", "department", "author", "tags")
    list_filter = ("document_type", "department", "uploaded_at")


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
