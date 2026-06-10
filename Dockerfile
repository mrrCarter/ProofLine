# Stage 1: Build the UI
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS ui-builder
WORKDIR /ui
COPY ui/package*.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# Stage 2: Python Backend
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97
WORKDIR /app

# Install system dependencies for OCR (Tesseract, PaddleOCR requirements)
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system proofline \
    && useradd --system --gid proofline --home-dir /app --shell /usr/sbin/nologin proofline

COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Copy backend code
COPY --chown=proofline:proofline . .

# Copy UI build from Stage 1 to a static folder
COPY --from=ui-builder --chown=proofline:proofline /ui/dist /app/static

# Expose port
EXPOSE 8000

# Run with uvicorn
USER proofline
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
