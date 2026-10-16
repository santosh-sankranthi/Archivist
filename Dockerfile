# syntax=docker/dockerfile:1

# --- stage 1: build Console v2 (Vite + React + TS) ---------------------------
FROM node:24-slim AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

# --- stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
# git: the ingest walks history by shelling out to git; mounted repos are
# owned by the host user while the container runs as root — trust them.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY pyproject.toml README.md ./
COPY --from=console /console/dist ./console/dist
EXPOSE 8787
# compose.yaml sets FALKORDB_HOST/PORT + ARCHIVIST_GRAPH; --bind 0.0.0.0 makes
# the server reachable through the published port (loopback-only by default).
CMD ["python", "scripts/serve.py", "--bind", "0.0.0.0"]
