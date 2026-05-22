from django.core.management.base import BaseCommand

from documents.embeddings import rebuild_document_embeddings
from documents.models import Document


class Command(BaseCommand):
    help = "Rebuild AWS Bedrock Titan embeddings for documents."

    def add_arguments(self, parser):
        parser.add_argument(
            "--document-id",
            type=int,
            help="Only rebuild embeddings for one document.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of documents to process.",
        )

    def handle(self, *args, **options):
        queryset = (
            Document.objects
            .exclude(extracted_text__isnull=True)
            .exclude(extracted_text__exact="")
            .exclude(extracted_text__startswith="TEXT_EXTRACTION_FAILED:")
            .order_by("id")
        )

        document_id = options.get("document_id")
        if document_id is not None:
            queryset = queryset.filter(id=document_id)

        limit = options.get("limit")
        if limit is not None:
            queryset = queryset[:limit]

        processed = 0
        succeeded = 0
        failed = 0

        for document in queryset:
            processed += 1
            file_name = document.file.name if document.file else ""

            try:
                chunks_created = rebuild_document_embeddings(document)
            except Exception as error:
                failed += 1
                self.stderr.write(
                    f"Document {document.id} {file_name}: ERROR {error}"
                )
                continue

            succeeded += 1
            self.stdout.write(
                f"Document {document.id} {file_name}: "
                f"{chunks_created} chunks created"
            )

        self.stdout.write(
            f"Done. processed={processed} "
            f"succeeded={succeeded} failed={failed}"
        )
