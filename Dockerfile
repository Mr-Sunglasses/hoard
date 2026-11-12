FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    MODELS_DIR=/models \
    CONFIG_DIR=/config \
    HF_HOME=/models/.hf-home \
    HF_HUB_DISABLE_TELEMETRY=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
