# ── Stage 1: Build tantivy-py with Lindera (Rust) ──────────
FROM python:3.12-slim AS tantivy-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl build-essential git pkg-config libssl-dev && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.cargo/bin:$PATH"

RUN pip install --no-cache-dir maturin

WORKDIR /build
ARG TANTIVY_PY_COMMIT=bbaa8c0c91529ca215fd82d864dac27cae3b269f
RUN git clone https://github.com/quickwit-oss/tantivy-py.git . && \
    git checkout ${TANTIVY_PY_COMMIT}
COPY patches/tantivy-py-lindera.patch /tmp/
RUN git apply /tmp/tantivy-py-lindera.patch
RUN maturin build --release --strip -o /dist

# ── Stage 2: Playwright browsers (cached independently) ────
FROM python:3.12-slim AS playwright-builder

RUN pip install --no-cache-dir playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN playwright install --with-deps chromium

# ── Stage 3: Install Python dependencies ───────────────────
FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir "uv>=0.6,<0.7"
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# Install tantivy wheel into the venv
COPY --from=tantivy-builder /dist/*.whl /tmp/
RUN uv pip install --python /app/.venv/bin/python --no-cache /tmp/*.whl

# ── Stage 4: Runtime ───────────────────────────────────────
FROM python:3.12-slim
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    echo "deb [trusted=yes arch=amd64] http://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" > /etc/apt/sources.list.d/mongodb-org-7.0.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        mongodb-database-tools \
        fonts-noto-cjk && \
    apt-get purge -y curl && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*
RUN groupadd --system app && useradd --system --gid app app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=playwright-builder /app/.playwright /app/.playwright
# Install only OS dependencies for Playwright Chromium (browser binaries already copied)
ENV PATH="/app/.venv/bin:$PATH" TZ=Asia/Tokyo \
    PLAYWRIGHT_BROWSERS_PATH=/app/.playwright
RUN playwright install-deps chromium && rm -rf /var/lib/apt/lists/*
COPY app/ ./app/
RUN mkdir -p /app/search_index /app/docsite_assets /app/search_index_docsites && \
    chown app:app /app/search_index /app/docsite_assets /app/search_index_docsites
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
