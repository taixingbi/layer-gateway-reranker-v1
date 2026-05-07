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

Expected:

```json
{"status":"ready"}
```

## 3) Basic rerank request

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

## 4) Rerank request with correlation headers

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

## 5) Negative test: correlation IDs in body must return 400

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
