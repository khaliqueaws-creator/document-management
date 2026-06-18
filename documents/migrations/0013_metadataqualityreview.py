import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0012_document_ai_explanation"),
    ]

    operations = [
        migrations.CreateModel(
            name="MetadataQualityReview",
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
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("complete", "Complete"),
                            ("failed", "Failed"),
                        ],
                        default="complete",
                        max_length=20,
                    ),
                ),
                (
                    "quality_score",
                    models.PositiveSmallIntegerField(default=0),
                ),
                (
                    "quality_level",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("excellent", "Excellent"),
                            ("good", "Good"),
                            ("needs_review", "Needs review"),
                            ("critical", "Critical"),
                        ],
                        max_length=20,
                    ),
                ),
                ("review_summary", models.TextField(blank=True)),
                ("issues", models.JSONField(blank=True, default=list)),
                ("model_id", models.CharField(blank=True, max_length=100)),
                ("error", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(auto_now=True)),
                (
                    "document",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metadata_quality_review",
                        to="documents.document",
                    ),
                ),
            ],
            options={
                "ordering": ["quality_score", "-reviewed_at"],
            },
        ),
    ]
