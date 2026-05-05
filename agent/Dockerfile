FROM python:3.12-slim

WORKDIR /app

# System deps:
#   build-essential      — for PyMySQL/cryptography native builds
#   libpoppler-cpp-dev   — Docling uses pypdfium2 (bundled PDF renderer, no poppler needed
#                          at runtime), but poppler-utils is a useful fallback debug tool
#   libgl1               — OpenCV (pulled by Docling's rapidocr) needs libGL.so.1
#   libglib2.0-0         — OpenCV also needs glib
# W2_ARCHITECTURE.md §2.3: Docling is self-hosted; PHI never leaves the container at OCR stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps separately for layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App code as a package at /app/agent (so `agent.main:app` resolves)
COPY . /app/agent/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
