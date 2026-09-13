FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-railway.txt ./
RUN pip install --no-cache-dir -r requirements-railway.txt
COPY . .
RUN mkdir -p /app/data/uploads
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 120 --graceful-timeout 30 --access-logfile -"]
