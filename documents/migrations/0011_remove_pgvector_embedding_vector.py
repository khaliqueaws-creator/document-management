from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0010_documentchunk_embedding_vector"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                "DROP INDEX IF EXISTS "
                "documents_documentchunk_embedding_vector_hnsw"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql=(
                "ALTER TABLE documents_documentchunk "
                "DROP COLUMN IF EXISTS embedding_vector"
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
