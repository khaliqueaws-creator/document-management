from django.urls import path
from . import views

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),

    # Home / Search
    path("", views.search_documents, name="home"),
    path("search/", views.search_documents, name="search"),
    path("ai-search/", views.ai_search, name="ai_search"),
    path("ask/", views.ask_documents, name="ask_documents"),

    # Normal upload
    path("upload/", views.upload_document, name="upload"),

    # OCR scanned image upload
    path(
        "upload-scanned/",
        views.upload_scanned_image,
        name="upload_scanned_image"
    ),

    # OCR review confirm/save
    path(
        "ocr/confirm/",
        views.confirm_document,
        name="confirm_document"
    ),

    path(
        "ocr/temp-preview/<str:filename>/",
        views.temp_scanned_preview,
        name="temp_scanned_preview"
    ),

    # OIDC routes
    path("login/", views.login, name="login"),

    path(
        "oidc/callback",
        views.oidc_callback,
        name="oidc_callback"
    ),

    path("logout/", views.logout, name="logout"),

    path("profile/", views.profile, name="profile"),

    path("audit/", views.audit_events, name="audit_events"),

    # Secure document viewing
    path(
        "view/<int:document_id>/",
        views.secure_document_view,
        name="secure_document_view"
    ),

    path(
        "edit/<int:document_id>/",
        views.edit_document_metadata,
        name="edit_document_metadata"
    ),

    path(
        "edit/<int:document_id>/ai/generate/",
        views.generate_ai_metadata,
        name="generate_ai_metadata"
    ),

    path(
        "edit/<int:document_id>/ai/accept/",
        views.accept_ai_metadata,
        name="accept_ai_metadata"
    ),

    path(
        "edit/<int:document_id>/ai/reject/",
        views.reject_ai_metadata,
        name="reject_ai_metadata"
    ),

    # Delete document
    path(
        "delete/<int:document_id>/",
        views.delete_document,
        name="delete_document"
    ),
]
