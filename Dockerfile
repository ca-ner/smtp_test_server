FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/

# HTTP (web UI + API) and SMTP capture ports
EXPOSE 8000 2525

ENV HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8000 \
    SMTP_HOST=0.0.0.0 \
    SMTP_PORT=2525 \
    PUBLIC_SMTP_HOST=localhost

CMD ["python", "-m", "app.main"]
