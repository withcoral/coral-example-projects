# Hello-service: small FastAPI app with a deliberate bug, instrumented with
# Sentry. Build for the cluster's CPU arch — same as the agent image:
#   docker build --platform linux/amd64 -t hello-service:<sha> .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "sentry-sdk[fastapi]>=2.18" \
        "httpx>=0.27"

COPY main.py ./

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
