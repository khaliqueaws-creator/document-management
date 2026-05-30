from django.core.management.base import BaseCommand

from documents.models import Document
from documents.opensearch_indexing import (
    OpenSearchIndexingError,
    reindex_document,
    ensure_indexes,
    get_opensearch_client,
)


class Command(BaseCommand):
    help = "Rebuild OpenSearch document and chunk indexes from PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--document-id",
            type=int,
            help="Only reindex one document.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of documents to process.",
        )
        parser.add_argument(
            "--create-indexes",
            action="store_true",
            help="Create OpenSearch indexes when they do not exist.",
        )

    def handle(self, *args, **options):
        queryset = Document.objects.all().order_by("id")

        document_id = options.get("document_id")
        if document_id is not None:
            queryset = queryset.filter(id=document_id)

        limit = options.get("limit")
        if limit is not None:
            queryset = queryset[:limit]

        client = get_opensearch_client()

        if options["create_indexes"]:
            ensure_indexes(client=client)

        processed = 0
        succeeded = 0
        failed = 0
        chunks_indexed = 0

        for document in queryset:
            processed += 1
            file_name = document.file.name if document.file else ""

            try:
                indexed_count = reindex_document(document, client=client)
            except OpenSearchIndexingError as error:
                failed += 1
                self.stderr.write(
                    f"Document {document.id} {file_name}: ERROR {error}"
                )
                continue

            succeeded += 1
            chunks_indexed += indexed_count
            self.stdout.write(
                f"Document {document.id} {file_name}: "
                f"{indexed_count} chunks indexed"
            )

        self.stdout.write(
            f"Done. processed={processed} succeeded={succeeded} "
            f"failed={failed} chunks_indexed={chunks_indexed}"
        )
