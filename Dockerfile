# Stage 1: Build the UI
FROM node:20-slim AS ui-builder
WORKDIR /ui
COPY ui/package*.json ./
RUN npm install
COPY ui/ ./
RUN npm run build

# Stage 2: Python Backend
FROM python:3.14-slim
WORKDIR /app

# Install system dependencies for OCR (Tesseract, PaddleOCR requirements)
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY . .

# Copy UI build from Stage 1 to a static folder
COPY --from=ui-builder /ui/dist /app/static

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["python", "main.py"]
