FROM python:3.10-slim

# System dependencies for compiling any requirements if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application files
COPY . .

# Expose port for the HTTP health check server
EXPOSE 8080

# Run the telegram bot
CMD ["python", "bot.py"]
