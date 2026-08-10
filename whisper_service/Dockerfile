FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN useradd -r -u 10001 -d /service -s /usr/sbin/nologin whisper \
    && mkdir -p /models /tmp/whisper \
    && chown -R whisper:whisper /models /tmp/whisper /service
USER whisper
EXPOSE 8090
CMD ["python", "app.py"]
