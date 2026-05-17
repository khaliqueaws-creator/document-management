from django.db import models


class Document(models.Model):
    document_type = models.CharField(max_length=100, blank=True)
    document_subtype = models.CharField(max_length=100,blank=True)
    ocr_text = models.TextField(blank=True)	
    department = models.CharField(max_length=100)
    author = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    tags = models.CharField(max_length=255, blank=True)

    file = models.FileField(upload_to="documents/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    extracted_text = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.file.name