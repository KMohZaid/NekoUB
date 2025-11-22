# Use Python 3.13 slim image for smaller size
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install build dependencies, Python packages, then clean up in one layer
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        gcc \
        build-essential \
    && pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    # Clean up build dependencies to reduce image size
    apt-get purge -y --auto-remove gcc build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

# Create private_plugins directory if it doesn't exist
RUN mkdir -p private_plugins

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the bot
CMD ["python", "main.py"]
