from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0005_auditevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="ai_document_type",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="document",
            name="ai_department",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="document",
            name="ai_tags",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="document",
            name="ai_summary",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="document",
            name="ai_suggestion_status",
            field=models.CharField(
                choices=[
                    ("not_requested", "Not requested"),
                    ("pending", "Pending"),
                    ("suggested", "Suggested"),
                    ("accepted", "Accepted"),
                    ("rejected", "Rejected"),
                    ("failed", "Failed"),
                ],
                default="not_requested",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="ai_suggested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="ai_error",
            field=models.TextField(blank=True),
        ),
    ]
