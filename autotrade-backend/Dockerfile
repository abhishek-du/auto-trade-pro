# ── AutoTrade Pro — Dockerfile ────────────────────────────────────────────────
# Builds the FastAPI application image.
# ta-lib requires the C library before the Python wheel can be installed.

FROM python:3.11-slim

# System deps: ta-lib C library + build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    libta-lib0-dev \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib C library from source (required for the Python wrapper)
RUN wget -q https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib && ./configure --prefix=/usr && make && make install \
    && cd .. && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
