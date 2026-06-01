# Smoke Test

Quick curl checks for `layer-gateway-reranker-v1`.

Set base URL (default port is `30182`):

```bash
export GW_URL="http://127.0.0.1:30182"
```

## 1) Health

```bash
curl -sS "$GW_URL/health"
```

Expected:

```json
{"status":"ok"}
```

## 2) Ready

```bash
curl -sS "$GW_URL/ready"
```

Expected (when all `RERANK_BACKENDS` upstreams respond `GET /health` with 200):

```json
{
  "status": "ready",
  "healthy_backends": 2,
  "total_backends": 2,
  "backends": {
    "reranker-node-1": "healthy",
    "reranker-node-2": "healthy"
  }
}
```

If any backend is down, HTTP `503` and `"status": "not_ready"` with per-backend `"healthy"` / `"unhealthy"`.

## 3) Version

```bash
curl -sS "$GW_URL/version" | jq .
```

Expected fields: `service`, `version`, `git_sha`, `git_branch`, `build_time`, `image`, `environment`.

## 4) Basic rerank request

```bash
curl -sS "$GW_URL/v1/rerank" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-reranker-v2-m3",
    "query": "What is Paris?",
    "documents": [
      "Paris is the capital of France.",
      "Berlin is the capital of Germany."
    ],
    "top_n": 2
  }'
```

Expected: HTTP `200` with backend rerank response JSON.

## 5) Rerank request with correlation headers

```bash
curl -sS "$GW_URL/v1/rerank" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: smoke-req-1" \
  -H "X-Trace-Id: smoke-trace-1" \
  -H "X-Session-Id: smoke-session-1" \
  -d '{
    "model": "BAAI/bge-reranker-v2-m3",
    "query": "What is Paris?",
    "documents": [
      "Paris is the capital of France.",
      "Berlin is the capital of Germany."
    ],
    "top_n": 2
  }'
```

Expected: HTTP `200`.

## 6) Rerank with `conversation_id`

Optional thread id in the JSON body (stripped before upstream; echoed in response on 2xx JSON):

```bash
curl -sS "$GW_URL/v1/rerank" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: smoke-req-2" \
  -H "X-Trace-Id: smoke-trace-2" \
  -H "X-Session-Id: smoke-session-2" \
  -d '{
    "model": "BAAI/bge-reranker-v2-m3",
    "query": "What is Paris?",
    "documents": [
      "Paris is the capital of France.",
      "Berlin is the capital of Germany."
    ],
    "top_n": 2,
    "conversation_id": "my-thread-1"
  }'
```

Expected: HTTP `200`; response JSON includes `conversation_id` and `is_new_conversation`.

## 7) Negative test: correlation IDs in body must return 400

```bash
curl -sS -i "$GW_URL/v1/rerank" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "BAAI/bge-reranker-v2-m3",
    "query": "What is Paris?",
    "documents": [
      "Paris is the capital of France.",
      "Berlin is the capital of Germany."
    ],
    "request_id": "not-allowed-in-body"
  }'
```

Expected:
- HTTP `400`
- Error message indicating correlation IDs must be passed via headers only.

## 8) k3s Deploy (NodePort)

From a host that can reach the dev NodePort (adjust IP if your server differs). `jq` is optional (drop `| jq .` if not installed).

```bash
curl -sS -X POST http://192.168.86.179:30182/v1/rerank \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: req-abc123" \
  -H "X-Session-Id: ses-xyz789" \
  -H "X-Trace-Id: trc-001" \
  -d '{
    "model": "BAAI/bge-reranker-v2-m3",
    "query": "what is taixing visa",
    "documents": [
      "Taixing visa is the visa service product used by Taixing.",
      "This sentence is unrelated to the user question."
    ],
    "top_n": 2
  }' | jq .
```

