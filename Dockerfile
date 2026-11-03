# Build Python dependencies separately so requirements changes do not rerun apt.
FROM python:3.13-slim AS builder

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        gcc \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# Keep build tools out of the runtime image.
FROM python:3.13-slim

WORKDIR /app
COPY --from=builder /install/ /usr/local/

# Copy application code
COPY . .

# Create private_plugins directory if it doesn't exist
RUN mkdir -p private_plugins

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "main.py"]
