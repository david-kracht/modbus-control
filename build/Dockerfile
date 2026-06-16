# Start clean - the platform is passed dynamically by build-project.sh
FROM python:3.11-slim-bookworm

# 1. Install system dependencies and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libffi-dev \
    libssl-dev \
    curl \
    git \
    cargo \
    rustc \
    && rm -rf /var/lib/apt/lists/*

# 2. Install uv (Globally for all users)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv && \
    mv /root/.local/bin/uvx /usr/local/bin/uvx && \
    rm -rf /root/.local

# 3. Copy the internal build script into the container
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# The script is executed by default when the container starts
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]