FROM python:3.11-slim

ARG APP_VERSION=dev
ARG GIT_SHA=unknown
ARG GIT_BRANCH=unknown
ARG BUILD_TIME=unknown
ARG BUILD_IMAGE=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    GIT_SHA=${GIT_SHA} \
    GIT_BRANCH=${GIT_BRANCH} \
    BUILD_TIME=${BUILD_TIME} \
    BUILD_IMAGE=${BUILD_IMAGE}

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY app /app/app
COPY README.md /app/README.md
RUN pip install --no-cache-dir .

EXPOSE 30182
CMD ["python", "-m", "app.main"]
