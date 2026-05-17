from django.urls import path
from . import views

urlpatterns = [

    # Home / Search
    path("", views.search_documents, name="home"),
    path("search/", views.search_documents, name="search"),

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

    # OIDC routes
    path("login/", views.login, name="login"),

    path(
        "oidc/callback",
        views.oidc_callback,
        name="oidc_callback"
    ),

    path("logout/", views.logout, name="logout"),

    path("profile/", views.profile, name="profile"),

    # Secure document viewing
    path(
        "view/<int:document_id>/",
        views.secure_document_view,
        name="secure_document_view"
    ),

    # Delete document
    path(
        "delete/<int:document_id>/",
        views.delete_document,
        name="delete_document"
    ),
]