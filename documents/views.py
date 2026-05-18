from django.core.files.storage import FileSystemStorage
from django.core.files import File
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, redirect
from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.db.models import Q

from .forms import DocumentForm
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
import pytesseract
from pdf2image import convert_from_path
from openpyxl import load_workbook


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def login(request):
    return oauth.okta.authorize_redirect(
        request,
        settings.OKTA_CALLBACK_URL
    )


def oidc_callback(request):
    token = oauth.okta.authorize_access_token(request)

    userinfo = token.get("userinfo")

    if not userinfo:
        userinfo = oauth.okta.userinfo(token=token)

    decoded_token = jwt.decode(
        token["id_token"],
        options={"verify_signature": False}
    )

    raw_groups = decoded_token.get("django_groups", [])

    group_names = []

    for group in raw_groups:
        if isinstance(group, str):
            group_names.append(group)

        elif isinstance(group, dict):
            name = group.get("profile", {}).get("name")

            if name:
                group_names.append(name)

    request.session["user"] = {
        "sub": userinfo.get("sub"),
        "name": userinfo.get("name"),
        "email": userinfo.get("email"),
        "preferred_username": userinfo.get("preferred_username"),
        "groups": userinfo.get("groups", []),
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

            if not document.author:
                document.author = (
                    request.session["user"].get("name")
                    or request.session["user"].get("email")
                )

            document.save()

            try:
                file_path = document.file.path
                document.extracted_text = extract_text_from_file(file_path)
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

        if not uploaded_file:
            return render(request, "upload_scanned.html", {
                "error": "Please select a file."
            })

        temp_dir = os.path.join(settings.MEDIA_ROOT, "temp")
        os.makedirs(temp_dir, exist_ok=True)

        fs = FileSystemStorage(
            location=temp_dir,
            base_url=settings.MEDIA_URL + "temp/"
        )

        temp_filename = fs.save(uploaded_file.name, uploaded_file)
        temp_file_path = os.path.join(temp_dir, temp_filename)
        temp_file_url = fs.url(temp_filename)

        ocr_text = extract_text_from_file(temp_file_path)
        metadata = extract_metadata_from_ocr(ocr_text)

        return render(request, "ocr_review.html", {
            "temp_file_path": temp_file_path,
            "temp_file_url": temp_file_url,
            "original_filename": uploaded_file.name,
            "ocr_text": ocr_text,
            "document_type": metadata.get("document_type", ""),
            "document_subtype": metadata.get("document_subtype", ""),
            
        })

    return render(request, "upload_scanned.html")

@okta_role_required(is_loader)
def confirm_document(request):

    if "user" not in request.session:
        return redirect("/login/")

    if request.method == "POST":

        temp_file_path = request.POST.get("temp_file_path")
        document_type = request.POST.get("document_type", "")
        document_subtype = request.POST.get("document_subtype", "")
        ocr_text = request.POST.get("ocr_text", "")

        if not temp_file_path or not os.path.exists(temp_file_path):
            raise Http404("Temporary scanned file not found")

        document = Document()
        document.document_type = document_type
        document.document_subtype = document_subtype        
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
    if document_type:
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

    return render(request, "search.html", {
        "documents": documents
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


def extract_text_from_file(file_path):
    text = ""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        reader = PdfReader(file_path)

        for page in reader.pages:
            text += page.extract_text() or ""
            text += "\n"

        if not text.strip():
            images = convert_from_path(file_path)

            for image in images:
                text += pytesseract.image_to_string(
                    image,
                    lang="eng+hin+urd"
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

        image = image.convert("L")

        text = pytesseract.image_to_string(
            image,
            lang="eng+hin+urd",
            config="--psm 6"
        )

    return text
