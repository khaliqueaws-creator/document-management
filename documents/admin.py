# Register your models here.
from django.contrib import admin
from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("document_type", "department", "author", "uploaded_at")
    search_fields = ("document_type", "department", "author", "tags")
    list_filter = ("document_type", "department", "uploaded_at")
    