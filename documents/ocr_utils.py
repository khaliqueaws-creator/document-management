import re
import pytesseract
from PIL import Image


def run_ocr(file_path):
    image = Image.open(file_path)
    return pytesseract.image_to_string(image, lang="eng+hin")


def extract_metadata(ocr_text):
    metadata = {
        "document_type": "",
    }

    match = re.search(
        r"Document\s*Type\s*[:\-]\s*(.+)",
        ocr_text,
        re.IGNORECASE
    )

    if match:
        metadata["document_type"] = match.group(1).strip()

    return metadata