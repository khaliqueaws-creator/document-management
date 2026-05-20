from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0004_document_ocr_language"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("document_name", models.CharField(blank=True, max_length=255)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("upload", "Upload"),
                            ("edit", "Edit metadata"),
                            ("delete", "Delete"),
                        ],
                        max_length=20,
                    ),
                ),
                ("actor_name", models.CharField(blank=True, max_length=150)),
                ("actor_email", models.EmailField(blank=True, max_length=254)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "document",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
