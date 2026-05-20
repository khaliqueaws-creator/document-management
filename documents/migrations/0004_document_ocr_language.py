from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0003_document_document_subtype_document_ocr_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="ocr_language",
            field=models.CharField(
                choices=[
                    ("english", "English"),
                    ("hindi", "Hindi"),
                    ("urdu", "Urdu"),
                ],
                default="english",
                max_length=20,
            ),
        ),
    ]
