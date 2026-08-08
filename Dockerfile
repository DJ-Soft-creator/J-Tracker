FROM python:3.12-slim

ENV TZ=Europe/Berlin

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .
COPY scripts/ /app/scripts/

# Create non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app \
    && chmod 750 /app/data

EXPOSE 4098

USER appuser

CMD ["python", "main.py"]
