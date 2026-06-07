FROM python:3.11-slim

# Installer libpq (requis par psycopg2-binary)
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["gunicorn", "app:app", "--workers", "2", "--bind", "0.0.0.0:8080", "--timeout", "120"]
