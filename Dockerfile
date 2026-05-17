FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    pkg-config \
    default-libmysqlclient-dev \
	tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    tesseract-ocr-urd \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/media && chmod -R 777 /app/media

EXPOSE 8000

CMD ["sh", "-c", "gunicorn docmanager.wsgi:application --bind 0.0.0.0:8000 --workers ${WORKERS:-1}"]