"""Pinned, local-only PaddleOCR model configuration."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_BASE = 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/'
MODELS = {
    'Det': ('PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx', '4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae'),
    'Cls': ('PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx', 'e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c'),
    'Rec': ('PP-OCRv6/rec/PP-OCRv6_rec_small.onnx', '6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884'),
}


def model_directory():
    return Path(os.getenv('OCR_MODEL_DIR', str(BASE_DIR / '.local-tools/paddle-models')))


def valid_model(path, digest):
    if not path.is_file():
        return False
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest() == digest


def verified_model_paths():
    paths = {}
    for role, (relative, digest) in MODELS.items():
        path = model_directory() / Path(relative).name
        if not valid_model(path, digest):
            raise RuntimeError('PaddleOCR model dosyaları eksik veya bozuk. Kurulumda python -m scripts.setup_paddle_ocr çalıştırın.')
        paths[role] = str(path)
    return paths
