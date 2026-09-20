FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data OMP_THREAD_LIMIT=1 OCR_ENGINE=paddle OCR_MODEL_DIR=/app/models/ocr
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-railway.txt ./
RUN pip install --no-cache-dir -r requirements-railway.txt
COPY . .
RUN python -m scripts.setup_paddle_ocr
RUN python -c "from services.paddle_ocr import create_engine; create_engine()"
RUN mkdir -p /app/data/uploads
EXPOSE 5000 8080
ENTRYPOINT ["python", "entrypoint.py"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
