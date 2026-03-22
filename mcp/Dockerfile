FROM python:3.12-slim AS builder

WORKDIR /app
RUN pip install --no-cache-dir "uv>=0.6,<0.7"
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

FROM python:3.12-slim
RUN groupadd --system app && useradd --system --gid app app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY app/ ./app/
ENV PATH="/app/.venv/bin:$PATH" TZ=Asia/Tokyo
USER app
EXPOSE 8001
# 単一ワーカー: statefulなSSEセッションを維持するため
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
