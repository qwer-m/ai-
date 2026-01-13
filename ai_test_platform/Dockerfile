# Use official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Set work directory
WORKDIR /app

# Replace Debian sources with Aliyun mirrors for faster download
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources || sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# Install Playwright browsers (for UI Automation)
# Use python -m playwright to ensure we use the installed module
RUN python -m playwright install --with-deps chromium

# Copy project
COPY . .

# Expose port
EXPOSE 8000

# Default command (overridden in docker-compose)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
