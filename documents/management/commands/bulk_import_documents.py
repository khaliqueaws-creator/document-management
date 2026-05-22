import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from documents.models import Document
from documents.views import extract_text_from_file, normalize_ocr_language


class Command(BaseCommand):
    help = "Bulk import documents from a local directory."

    def add_arguments(self, parser):
        parser.add_argument("source_dir", help="Directory to import from.")
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of supported files to import.",
        )
        parser.add_argument(
            "--department",
            default="Test",
            help="Department value for imported documents.",
        )
        parser.add_argument(
            "--document-type",
            default="Imported",
            help="Document type value for imported documents.",
        )
        parser.add_argument(
            "--author",
            default="Bulk Import",
            help="Author value for imported documents.",
        )
        parser.add_argument(
            "--tags",
            default="test, synthetic",
            help="Tags value for imported documents.",
        )
        parser.add_argument(
            "--ocr-language",
            default=Document.OCR_LANGUAGE_ENGLISH,
            choices=[
                choice[0] for choice in Document.OCR_LANGUAGE_CHOICES
            ],
            help="OCR language for image and scanned PDF extraction.",
        )

    def handle(self, *args, **options):
        source_dir = Path(options["source_dir"])

        if not source_dir.exists() or not source_dir.is_dir():
            raise CommandError(f"Source directory does not exist: {source_dir}")

        supported_files = self.get_supported_files(source_dir)
        limit = options.get("limit")

        if limit is not None:
            supported_files = supported_files[:limit]

        processed = 0
        imported = 0
        failed = 0

        for source_path in supported_files:
            processed += 1

            try:
                document = self.import_file(source_path, options)
            except Exception as error:
                failed += 1
                self.stderr.write(f"{source_path}: ERROR {error}")
                continue

            imported += 1
            self.stdout.write(
                f"Document {document.id} {document.file.name}: imported"
            )

        self.stdout.write(
            f"Done. processed={processed} imported={imported} failed={failed}"
        )

    def get_supported_files(self, source_dir):
        supported_extensions = set(settings.ALLOWED_DOCUMENT_EXTENSIONS)
        return [
            path
            for path in sorted(source_dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in supported_extensions
        ]

    def import_file(self, source_path, options):
        ocr_language = normalize_ocr_language(options["ocr_language"])
        document = Document(
            document_type=options["document_type"],
            ocr_language=ocr_language,
            department=options["department"],
            author=options["author"],
            tags=options["tags"],
        )

        content_type, _ = mimetypes.guess_type(source_path.name)
        with source_path.open("rb") as file_handle:
            document.file.save(
                source_path.name,
                File(file_handle),
                save=False,
            )

        document.save()

        try:
            document.extracted_text = extract_text_from_file(
                document.file.path,
                ocr_language,
            )
        except Exception as error:
            document.extracted_text = f"TEXT_EXTRACTION_FAILED: {error}"

        document.description = (
            f"Bulk imported from {source_path}. "
            f"Content type: {content_type or 'unknown'}."
        )
        document.save(update_fields=["extracted_text", "description"])

        return document
