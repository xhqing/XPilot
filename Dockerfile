FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install xray
RUN curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh -o /tmp/install-release.sh && \
    bash /tmp/install-release.sh install && \
    rm /tmp/install-release.sh

# Copy project
COPY . /app

# Install Python dependencies
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir pytest ruff

# Default command
CMD ["xpilot", "--help"]
