FROM python:3.11-slim

# System deps — ffmpeg + chromium for Spotify
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers
RUN playwright install chromium --with-deps 2>/dev/null || true

# Copy bot files
COPY bot.py .
COPY cookies.txt* ./

# Download dir
RUN mkdir -p /tmp/ytdl downloads

# Non-root user
RUN useradd -m botuser \
    && chown -R botuser:botuser /app /tmp/ytdl
USER botuser

# Koyeb health check port
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-u", "bot.py"]
