# Generated to align the MySQL schema with the current Document model.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0002_document_extracted_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="document_subtype",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="document",
            name="ocr_text",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
    ]
