FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install Python dependencies first (leverages Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files (LICENSE is required for license verification)
COPY . .

# Create necessary runtime directories
RUN mkdir -p data logs uploads temp

CMD ["python", "main.py"]
