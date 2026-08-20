FROM python:3.11-slim

WORKDIR /app

# System deps — cached as a layer; only re-runs if this block changes
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# pip install — cached as a layer; only re-runs when requirements.txt changes.
# COPY requirements.txt is intentionally before COPY . . so that code changes
# don't invalidate this layer (Railway caches Docker layers between builds).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

CMD ["./start.sh"]
