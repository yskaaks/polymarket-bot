FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application code
COPY config/ config/
COPY src/ src/
COPY scripts/ scripts/

ENTRYPOINT ["python", "-m", "src.strategies.uma_arb_strategy"]
