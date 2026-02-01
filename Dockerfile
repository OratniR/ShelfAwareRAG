# Start from a lightweight Python 3.13 image (it's multi-arch, so it works on Pi)
FROM python:3.13-slim

# Set a working directory inside the container
WORKDIR /app

# ... (lines 1-13 are the same) ...

# Install system dependencies (for llama-cpp-python's CPU acceleration)
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    curl \
    libopenblas-dev \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# === START FIX ===

# Install uv (our package manager) and move its binaries to /usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx

# Add .venv/bin to PATH so python/streamlit commands work directly
ENV PATH="/app/.venv/bin:$PATH"

# Copy ONLY the dependency files first
COPY pyproject.toml uv.lock ./

# Install Python packages using uv
# Set CMAKE_ARGS to link llama-cpp-python against OpenBLAS
RUN CMAKE_ARGS="-DLLAMA_BLAS=ON -DLLAMA_BLAS_VENDOR=OpenBLAS" uv sync

# ... (the rest of your Dockerfile is correct) ...

# Copy all your application code into the container
COPY src/ ./src/

# Expose the port the app will run on
EXPOSE 8000

# The command to run when the container starts
CMD ["uv", "run", "uvicorn", "src.shelf_aware.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]