from django.core.files.storage import FileSystemStorage
from django.core.files import File
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.urls import reverse

from .forms import (
    DocumentForm,
    DocumentMetadataForm,
    validate_uploaded_file,
)
from .models import Document
from .auth import oauth
from .permissions import (
    okta_role_required,
    is_viewer,
    is_loader,
    is_admin,
)

import os
import json
import jwt
from pypdf import PdfReader
from docx import Document as DocxDocument
from PIL import Image
from PIL import ImageOps
import pytesseract
from pdf2image import convert_from_path
from openpyxl import load_workbook


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def get_temp_upload_path(filename):
    temp_dir = os.path.abspath(os.path.join(settings.MEDIA_ROOT, "temp"))
    file_path = os.path.abspath(os.path.join(temp_dir, os.path.basename(filename)))

    if not file_path.startswith(temp_dir + os.sep):
        raise Http404("Temporary scanned file not found")

    return file_path


def get_ocr_language_choices():
    return Document.OCR_LANGUAGE_CHOICES


def get_ocr_language_label(ocr_language):
    return dict(Document.OCR_LANGUAGE_CHOICES).get(ocr_language, "English")


def normalize_ocr_language(ocr_language):
    allowed_languages = {
        value for value, label in Document.OCR_LANGUAGE_CHOICES
    }

    if ocr_language in allowed_languages:
        return ocr_language

    return Document.OCR_LANGUAGE_ENGLISH


def get_tesseract_language(ocr_language):
    language_map = {
        Document.OCR_LANGUAGE_ENGLISH: "eng",
        Document.OCR_LANGUAGE_HINDI: "hin",
        Document.OCR_LANGUAGE_URDU: "urd",
    }

    return language_map.get(
        normalize_ocr_language(ocr_language),
        "eng"
    )


def get_tesseract_config(ocr_language):
    if normalize_ocr_language(ocr_language) == Document.OCR_LANGUAGE_URDU:
        return "--psm 6 -c preserve_interword_spaces=1"

    return "--psm 6"


def preprocess_image_for_ocr(image):
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")

    width, height = image.size

    if max(width, height) < 1800:
        image = image.resize((width * 2, height * 2))

    image = ImageOps.autocontrast(image)
    return image.point(lambda pixel: 255 if pixel > 175 else 0)


def login(request):
    return oauth.okta.authorize_redirect(
        request,
        settings.OKTA_CALLBACK_URL
    )


def normalize_okta_groups(raw_groups):
    group_names = []

    for group in raw_groups or []:
        if isinstance(group, str):
            group_names.append(group)

        elif isinstance(group, dict):
            name = group.get("profile", {}).get("name") or group.get("name")

            if name:
                group_names.append(name)

    return group_names


def oidc_callback(request):
    token = oauth.okta.authorize_access_token(request)

    userinfo = token.get("userinfo")

    if not userinfo:
        userinfo = oauth.okta.userinfo(token=token)

    decoded_token = jwt.decode(
        token["id_token"],
        options={"verify_signature": False}
    )

    group_names = []

    for claim_name in ("django_groups", "groups"):
        group_names.extend(normalize_okta_groups(decoded_token.get(claim_name)))

    group_names.extend(normalize_okta_groups(userinfo.get("groups")))
    group_names = sorted(set(group_names))

    request.session["user"] = {
        "sub": userinfo.get("sub"),
        "name": userinfo.get("name"),
        "email": userinfo.get("email"),
        "preferred_username": userinfo.get("preferred_username"),
        "groups": group_names,
    }

    return redirect("/")


def logout(request):
    django_logout(request)
    request.session.flush()

    return redirect(settings.OKTA_LOGOUT_REDIRECT_URL)


@okta_role_required(is_viewer)
def profile(request):
    return render(request, "profile.html", {
        "user": request.session["user"]
    })


@okta_role_required(is_loader)
def upload_document(request):
    if request.method == "POST":
        form = DocumentForm(request.POST, request.FILES)

        if form.is_valid():
            document = form.save(commit=False)
            document.ocr_language = normalize_ocr_language(
                document.ocr_language
            )

            if not document.author:
                document.author = (
                    request.session["user"].get("name")
                    or request.session["user"].get("email")
                )

            document.save()

            try:
                file_path = document.file.path
                document.extracted_text = extract_text_from_file(
                    file_path,
                    document.ocr_language
                )
                document.save()

            except Exception as e:
                document.extracted_text = f"TEXT_EXTRACTION_FAILED: {str(e)}"
                document.save()

            return redirect("/")

    else:
        form = DocumentForm()

    return render(request, "upload.html", {"form": form})

@okta_role_required(is_loader)
def upload_scanned_image(request):

    if "user" not in request.session:
        return redirect("/login/")

    if request.method == "POST":

        uploaded_file = request.FILES.get("file")
        ocr_language = normalize_ocr_language(
            request.POST.get("ocr_language")
        )

        if not uploaded_file:
            return render_upload_scanned(
                request,
                "Please select a file.",
                ocr_language
            )

        try:
            validate_uploaded_file(
                uploaded_file,
                settings.ALLOWED_SCANNED_IMAGE_EXTENSIONS
            )
        except ValidationError as error:
            return render_upload_scanned(
                request,
                error.messages[0],
                ocr_language
            )

        temp_dir = os.path.join(settings.MEDIA_ROOT, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        fs = FileSystemStorage(
            location=temp_dir
        )

        temp_filename = fs.save(uploaded_file.name, uploaded_file)
        temp_file_path = os.path.join(temp_dir, temp_filename)
        temp_file_url = reverse(
            "temp_scanned_preview",
            kwargs={"filename": temp_filename}
        )

        ocr_text = extract_text_from_file(temp_file_path, ocr_language)
        metadata = extract_metadata_from_ocr(ocr_text)

        return render(request, "ocr_review.html", {
            "temp_filename": temp_filename,
            "temp_file_url": temp_file_url,
            "original_filename": uploaded_file.name,
            "ocr_language": ocr_language,
            "ocr_language_label": get_ocr_language_label(ocr_language),
            "ocr_text": ocr_text,
            "document_type": metadata.get("document_type", ""),
            "document_subtype": metadata.get("document_subtype", ""),
            
        })

    return render_upload_scanned(request)


def render_upload_scanned(
    request,
    error=None,
    selected_ocr_language=Document.OCR_LANGUAGE_ENGLISH
):
    return render(request, "upload_scanned.html", {
        "error": error,
        "ocr_language_choices": get_ocr_language_choices(),
        "selected_ocr_language": normalize_ocr_language(
            selected_ocr_language
        ),
    })


@okta_role_required(is_loader)
def temp_scanned_preview(request, filename):
    file_path = get_temp_upload_path(filename)

    if not os.path.exists(file_path):
        raise Http404("Temporary scanned file not found")

    return FileResponse(
        open(file_path, "rb"),
        as_attachment=False,
        filename=os.path.basename(file_path)
    )


@okta_role_required(is_loader)
def confirm_document(request):

    if "user" not in request.session:
        return redirect("/login/")

    if request.method == "POST":

        temp_filename = request.POST.get("temp_filename")
        document_type = request.POST.get("document_type", "")
        document_subtype = request.POST.get("document_subtype", "")
        ocr_language = normalize_ocr_language(
            request.POST.get("ocr_language")
        )
        ocr_text = request.POST.get("ocr_text", "")

        if not temp_filename:
            raise Http404("Temporary scanned file not found")

        temp_file_path = get_temp_upload_path(temp_filename)

        if not os.path.exists(temp_file_path):
            raise Http404("Temporary scanned file not found")

        document = Document()
        document.document_type = document_type
        document.document_subtype = document_subtype
        document.ocr_language = ocr_language
        document.extracted_text = ocr_text

        if not document.author:
            document.author = (
                request.session["user"].get("name")
                or request.session["user"].get("email")
            )

        with open(temp_file_path, "rb") as f:
            document.file.save(
                os.path.basename(temp_file_path),
                File(f),
                save=False
            )

        document.save()

        try:
            os.remove(temp_file_path)
        except Exception:
            pass

        return redirect("search")

    return redirect("upload_scanned_image")


@okta_role_required(is_viewer)
def search_documents(request):
    documents = Document.objects.all().order_by("-uploaded_at")

    document_type = request.GET.get("document_type")
    document_subtype = request.GET.get("document_subtype")
    department = request.GET.get("department")
    author = request.GET.get("author")
    tags = request.GET.get("tags")
    content = request.GET.get("content")

    if document_type:
        documents = documents.filter(document_type__icontains=document_type)
    if document_subtype:
        documents = documents.filter(document_subtype__icontains=document_subtype)    

    if department:
        documents = documents.filter(department__icontains=department)

    if author:
        documents = documents.filter(author__icontains=author)

    if tags:
        documents = documents.filter(tags__icontains=tags)

    if content:
        documents = documents.filter(
            Q(extracted_text__icontains=content)
        )

    paginator = Paginator(documents, settings.DOCUMENTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)

    return render(request, "search.html", {
        "documents": page_obj,
        "page_obj": page_obj,
        "query_string": query_params.urlencode(),
        "total_documents": paginator.count,
    })


@okta_role_required(is_viewer)
def secure_document_view(request, document_id):
    try:
        document = Document.objects.get(id=document_id)

    except Document.DoesNotExist:
        raise Http404("Document not found")

    file_path = document.file.path

    if not os.path.exists(file_path):
        raise Http404("File not found")

    return FileResponse(
        open(file_path, "rb"),
        as_attachment=False,
        filename=os.path.basename(file_path)
    )


@okta_role_required(is_loader)
def edit_document_metadata(request, document_id):
    try:
        document = Document.objects.get(id=document_id)

    except Document.DoesNotExist:
        raise Http404("Document not found")

    if request.method == "POST":
        form = DocumentMetadataForm(request.POST, instance=document)

        if form.is_valid():
            form.save()
            return redirect("search")

    else:
        form = DocumentMetadataForm(instance=document)

    file_extension = os.path.splitext(document.file.name)[1].lower()

    return render(request, "edit_metadata.html", {
        "document": document,
        "form": form,
        "can_preview_inline": file_extension in [
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".bmp",
        ],
    })


@okta_role_required(is_admin)
def delete_document(request, document_id):
    try:
        document = Document.objects.get(id=document_id)

    except Document.DoesNotExist:
        raise Http404("Document not found")

    file_path = document.file.path

    if file_path and os.path.exists(file_path):
        os.remove(file_path)

    document.delete()

    return redirect("/")

def extract_metadata_from_ocr(ocr_text):
    metadata = {
        "document_type": "",
        "document_subtype": "",
    }

    for line in ocr_text.splitlines():
        line_lower = line.lower()

        if "document type" in line_lower:
            parts = line.split(":", 1)
            if len(parts) == 2:
                metadata["document_type"] = parts[1].strip()
            else:
                metadata["document_type"] = line.strip()
        elif "document subtype" in line_lower:
            parts = line.split(":", 1)
            if len(parts) == 2:
                metadata["document_subtype"] = parts[1].strip()        

    return metadata


def extract_text_from_file(file_path, ocr_language=Document.OCR_LANGUAGE_ENGLISH):
    text = ""
    ext = os.path.splitext(file_path)[1].lower()
    tesseract_language = get_tesseract_language(ocr_language)
    tesseract_config = get_tesseract_config(ocr_language)

    if ext == ".pdf":
        reader = PdfReader(file_path)

        for page in reader.pages:
            text += page.extract_text() or ""
            text += "\n"

        if not text.strip():
            images = convert_from_path(file_path)

            for image in images:
                image = preprocess_image_for_ocr(image)
                text += pytesseract.image_to_string(
                    image,
                    lang=tesseract_language,
                    config=tesseract_config
                )
                text += "\n"

    elif ext == ".docx":
        doc = DocxDocument(file_path)

        for para in doc.paragraphs:
            text += para.text + "\n"

    elif ext == ".txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

    elif ext == ".xlsx":
        workbook = load_workbook(file_path, data_only=True)

        for sheet in workbook.worksheets:
            text += f"\n--- Sheet: {sheet.title} ---\n"

            for row in sheet.iter_rows(values_only=True):
                row_text = " ".join(
                    str(cell) for cell in row if cell is not None
                )
                text += row_text + "\n"

    elif ext in [".png", ".jpg", ".jpeg", ".tiff", ".bmp"]:
        image = Image.open(file_path)

        image = preprocess_image_for_ocr(image)

        text = pytesseract.image_to_string(
            image,
            lang=tesseract_language,
            config=tesseract_config
        )

    return text
