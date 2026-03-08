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

# === LLM サーバー (C++ バイナリを直接ビルド: 最軽量・最速) ===
FROM base AS llm

# 1. ビルドツールのインストール
# pkg-config を追加 (これがないと OpenBLAS が見つかりません)
RUN apt-get update && apt-get install -y \
    git \
    build-essential \
    cmake \
    libopenblas-dev \
    curl \
    pkg-config \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. llama.cpp のビルド
# --depth 1: 最新コミットだけ取得してダウンロード時間を短縮
# -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS: Raspberry PiのCPU最適化に必須
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp \
    && cd llama.cpp \
    && cmake -B build \
        -DGGML_BLAS=ON \
        -DGGML_BLAS_VENDOR=OpenBLAS \
        -DBUILD_SHARED_LIBS=OFF \
    && cmake --build build --config Release -j$(nproc) --target llama-server \
    && cp build/bin/llama-server /usr/local/bin/llama-server \
    && cd .. \
    && rm -rf llama.cpp

# 3. 実行コマンド
# llama.cpp server は Python ではなくバイナリを直接叩きます
# --model パスは docker-compose.yml で指定されたものを参照します
EXPOSE 8001
CMD ["llama-server", "--host", "0.0.0.0", "--port", "8001", "--model", "/models/Qwen3.5-0.8B-Q4_K_M.gguf", "--ctx-size", "2048", "--n-gpu-layers", "0"]

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

# === Benchmark (api + dev deps + tests) ===
FROM base AS benchmark
RUN uv sync --extra api
COPY src/ ./src/
COPY tests/ ./tests/
CMD ["uv", "run", "pytest", "tests/benchmark/", "-v", "-s", "-m", "benchmark"]
