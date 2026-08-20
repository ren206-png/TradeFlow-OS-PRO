#!/bin/bash
set -e

echo "==> PORT=${PORT:-8000}"
echo "==> DATABASE_URL prefix: ${DATABASE_URL:0:30}..."
echo "==> Running database migrations..."
alembic upgrade head
echo "==> Migrations complete. Starting server on port ${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 --log-level info
