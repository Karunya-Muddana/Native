FROM python:3.11-slim
WORKDIR /sandbox

RUN pip install --no-cache-dir \
    numpy \
    pandas \
    matplotlib \
    scipy \
    scikit-learn \
    openpyxl \
    python-docx \
    python-pptx \
    Pillow \
    asteval \
    pypdf \
    pdfplumber \
    pytesseract \
    tabulate \
    requests \
    PyMuPDF

# OCR engine: PP-OCRv4 detection + recognition running on ONNX Runtime.
# No PaddlePaddle, no torch — the wheel ships the models, so nothing is
# fetched at run time (the OCR container runs with the network disabled).
# opencv-python-headless replaces opencv-python: same cv2 module, without the
# GUI shared libraries that are not present in a slim image.
RUN pip install --no-cache-dir rapidocr-onnxruntime==1.4.4 \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless

# The bundled recognition model is the Chinese one, which does not emit spaces
# on Latin text ("INSPECTIONREPORT"). The English model is fetched here, at
# build time, because the container has no network when it actually runs.
ADD https://huggingface.co/SWHL/RapidOCR/resolve/main/PP-OCRv3/en_PP-OCRv3_rec_infer.onnx \
    /models/en_PP-OCRv3_rec_infer.onnx

# Tesseract stays as a fallback: if the ONNX engine fails to load for any
# reason, run_ocr degrades to it rather than returning nothing.
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Fail the build, not the first OCR call, if the models are missing.
RUN python -c "\
import os, pathlib, rapidocr_onnxruntime as r; \
bundled = list(pathlib.Path(r.__file__).parent.rglob('*.onnx')); \
assert len(bundled) >= 3, f'bundled models missing: {bundled}'; \
size = os.path.getsize('/models/en_PP-OCRv3_rec_infer.onnx'); \
assert size > 1_000_000, f'english rec model looks wrong: {size} bytes'; \
print(f'OCR ready: {len(bundled)} bundled models + english rec ({size//1000} KB)')"

RUN mkdir -p /sandbox/output
