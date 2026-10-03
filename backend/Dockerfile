FROM python:3.11-slim

# Install system dependencies including ffmpeg for audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency requirements
COPY requirements.txt .

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download LiveKit models (Silero VAD, etc.) into the container image
RUN python -m livekit.agents download-files || true

# Copy backend source code
COPY . .

# Ensure start script is executable
RUN chmod +x start.sh

# Expose FastAPI Token Server port
EXPOSE 8000

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

CMD ["./start.sh"]
