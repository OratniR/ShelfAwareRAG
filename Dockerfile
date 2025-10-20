# Start from a lightweight Python 3.13 image (it's multi-arch, so it works on Pi)
FROM python:3.13-slim

# Set a working directory inside the container
WORKDIR /app

# Install system dependencies (for llama-cpp-python's CPU acceleration)
RUN apt-get update && apt-get install -y \
    libopenblas-dev \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install uv (our package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.cargo/bin:$PATH"

# Copy ONLY the dependency files first
COPY pyproject.toml uv.lock ./

# Install Python packages using uv
# This step is cached, so it only re-runs if your dependencies change
RUN uv sync

# Copy all your application code into the container
COPY src/ ./src/

# Expose the port the app will run on
EXPOSE 8000

# The command to run when the container starts
CMD ["uv", "run", "uvicorn", "src.shelf_aware.main:app", "--host", "0.0.0.0", "--port", "8000"]