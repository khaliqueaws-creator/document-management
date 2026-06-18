from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0011_remove_pgvector_embedding_vector"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="ai_explanation",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
