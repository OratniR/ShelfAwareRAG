# =============================================================
# Multi-stage Dockerfile
# 共通ベース → サービスごとのステージに分岐
# =============================================================

# === 共通ベースステージ ===
FROM python:3.13-slim AS base
WORKDIR /app

# 最小限のシステム依存 (curl は uv のインストールに必要)
RUN apt-get update && apt-get install -y \
    curl \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# uv (パッケージマネージャ) のインストール
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# .venv/bin を PATH に追加
ENV PATH="/app/.venv/bin:$PATH"

# 依存ファイルをコピー (キャッシュ効率化のためコードより先に)
COPY pyproject.toml uv.lock ./

# === LLM サーバー (CMake + OpenBLAS が必要) ===
FROM base AS llm
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libopenblas-dev \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
RUN CMAKE_ARGS="-DLLAMA_BLAS=ON -DLLAMA_BLAS_VENDOR=OpenBLAS" \
    uv sync --extra llm --no-dev
COPY src/ ./src/
EXPOSE 8001
CMD ["uv", "run", "python", "-m", "llama_cpp.server", "--host", "0.0.0.0", "--port", "8001"]

# === RAG API ===
FROM base AS api
RUN uv sync --extra api --no-dev
COPY src/ ./src/
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "src.shelf_aware.main:app", "--host", "0.0.0.0", "--port", "8000"]

# === Dashboard (軽量) ===
FROM base AS dashboard
RUN uv sync --extra dashboard --no-dev
COPY src/ ./src/
EXPOSE 8501
CMD ["uv", "run", "streamlit", "run", "src/shelf_aware/dashboard.py", "--server.port=8501", "--server.address=0.0.0.0"]
