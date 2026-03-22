FROM python:3.12-slim

ENV TZ=Asia/Tokyo
WORKDIR /app

RUN pip install "uv>=0.6,<0.7"

COPY pyproject.toml .
RUN uv sync --no-dev

COPY app/ ./app/

EXPOSE 8001
# 単一ワーカー: statefulなSSEセッションを維持するため
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
