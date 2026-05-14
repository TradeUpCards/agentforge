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
    PYTHONDONTWRITEBYTECODE=1 \
    # Sentence-transformers / HuggingFace model cache (all-MiniLM-L6-v2,
    # BAAI reranker).  Persisted via a named Docker volume so the ~80 MB
    # model download survives container restarts.  W2_ARCHITECTURE.md §4.1.
    HF_HOME=/app/.hf_cache

# Container provenance — passed in by the build invocation
# (`.deploy/bootstrap.sh` populates from `git rev-parse HEAD`). Surfaced
# at runtime via /health so external monitors (W3 Clinical Red Team
# Platform daemon, ops alerting) have a deterministic version fingerprint
# instead of inferring from response shape.
#
# IMPORTANT: ARG/ENV go AT THE BOTTOM (after the expensive RUN pip install
# and COPY . layers). Docker invalidates all layers BELOW an ARG when the
# ARG value changes; placing it at the top forces a full rebuild on every
# fingerprint bump (~10 min). Placing it here makes fingerprint-only
# rebuilds a fast cache-walk (~30s). 2026-05-14 fix.
ARG VERSION_SHA=unknown
ENV VERSION_SHA=$VERSION_SHA

EXPOSE 8000

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
