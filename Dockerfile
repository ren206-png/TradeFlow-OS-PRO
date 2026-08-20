# Inherits OS deps + all pip packages from the pre-built base image.
# Base is rebuilt by .github/workflows/build-base.yml only when requirements.txt changes.
# Normal code pushes go straight to COPY + chmod — no apt-get or pip install needed.
#
# To rebuild the base manually: GitHub → Actions → "Build base image" → Run workflow.
# Fallback (if GHCR is unavailable): revert FROM to python:3.11-slim and restore
# the apt-get + pip install block from Dockerfile.base.

FROM ghcr.io/ren206-png/tradeflow-base:latest

WORKDIR /app

COPY . .
RUN chmod +x start.sh

CMD ["./start.sh"]
