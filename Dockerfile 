# Use a lightweight Python base image
FROM python:3.10-slim

# Prevent Python from writing pyc files and keep stdout unbuffered
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies: FFmpeg (for video/audio processing) and curl/unzip (for Deno)
RUN apt-get update && \
    apt-get install -y ffmpeg curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Download and install Deno locally for yt-dlp JavaScript challenges
RUN curl -fsSL https://deno.land/install.sh | sh

# Add Deno to the system PATH so yt-dlp can find it automatically
ENV PATH="/root/.deno/bin:$PATH"

# Set the working directory inside the container
WORKDIR /app

# Install all the required Python libraries
# We are installing pyrofork as you requested, along with tgcrypto for faster uploads
RUN pip install --no-cache-dir pyrofork tgcrypto yt-dlp requests

# Copy all your files (main.py, cookies.txt, etc.) into the container
COPY . .

# Create the downloads directory just in case
RUN mkdir -p downloads

# Run the bot
CMD ["python3", "main.py"]
