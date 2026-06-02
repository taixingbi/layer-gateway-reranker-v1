# Docker Guide

This guide covers:
- Running the gateway locally with Docker
- Publishing the image to GHCR

## Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- A `.env` file in repo root (you can start from `.env.example`)
- Reachable rerank backends configured in `RERANK_BACKENDS`

## 1) Local Docker Deploy (single container)

Build image from project root:

```bash
docker build -t layer-gateway-reranker-v1:local .
```

Run container:

```bash
docker run --rm \
  --name layer-gateway-reranker-v1 \
  --env-file .env \
  -p 30182:30182 \
  layer-gateway-reranker-v1:local
```

Verify:

```bash
curl http://localhost:30182/health
```

Expected response:

```json
{"status":"ok"}
```

## 2) Local Docker Deploy (compose)

Use compose from project root:

```bash
docker compose up --build
```

Run in background:

```bash
docker compose up -d --build
```

Stop:

```bash
docker compose down
```

## 3) Test the Rerank Endpoint

```bash
curl -X POST http://localhost:30182/v1/rerank \
  -H "X-Request-Id: request_id_1" \
  -H "X-Trace-Id: trace_id_1" \
  -H "X-Session-Id: session_id_1" \
  -H "Content-Type: application/json" \
  -d '{
    "model":"BAAI/bge-reranker-v2-m3",
    "query":"What is Paris?",
    "documents":[
      "Paris is the capital of France.",
      "Berlin is the capital of Germany."
    ],
    "top_n":2
  }'
```

## 4) Publish to GHCR

Set your GHCR namespace and target tag:

```bash
export IMAGE=ghcr.io/taixingbi/layer-gateway-reranker-v1
export IMAGE_NAME=layer-gateway-reranker-v1
export IMAGE_TAG=v1.0.0
```

Build and tag:

```bash
docker build -t ${IMAGE}/${IMAGE_NAME}:${IMAGE_TAG} .
docker tag ${IMAGE}/${IMAGE_NAME}:${IMAGE_TAG} ${IMAGE}/${IMAGE_NAME}:latest
```

Login and push:

```bash
docker login
docker push ${IMAGE}/${IMAGE_NAME}:${IMAGE_TAG}
docker push ${IMAGE}/${IMAGE_NAME}:latest
```

## 5) Pull and Run from GHCR

```bash
docker pull ${IMAGE}/${IMAGE_NAME}:latest
docker run --rm \
  --name layer-gateway-reranker-v1 \
  --env-file .env \
  -p 30182:30182 \
  ${IMAGE}/${IMAGE_NAME}:latest
```

## Troubleshooting

- `503 No healthy backend available`: verify `RERANK_BACKENDS` and backend health.
- `429 Gateway busy`: increase `ADMISSION_MAX_CONCURRENT` or `ADMISSION_WAIT_TIMEOUT_MS`.
