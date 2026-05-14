# layer-gateway-reranker-v1

Request-level routing gateway for `/v1/rerank` across multiple vLLM backends.

## Endpoints

- `POST /v1/rerank`
- `GET /health`
- `GET /ready`
- `GET /metrics`

## Correlation headers (recommended)

- `X-Request-Id`
- `X-Trace-Id`
- `X-Session-Id`

Optional JSON field **`conversation_id`**: thread id for the rerank call; omitted or blank values get a generated `conv_…` id. See `docs/conversation-id.md`.

Omitted or blank values are auto-filled with UUIDs for logging and upstream forwarding. JSON logs show `"-"` for `request_id` / `trace_id` / `session_id` when a log line omits them or passes an empty string (see `docs/request-response-and-logging.md`).

## Configuration

Set values via environment variables (see `.env.example`):

- `RERANK_BACKENDS` (`name=url,name=url`)
- `TIMEOUT_CONNECT_MS`
- `TIMEOUT_READ_MS`
- `RETRY_MAX_ATTEMPTS`
- `ADMISSION_MAX_CONCURRENT`
- `ADMISSION_WAIT_TIMEOUT_MS`
- `CB_FAILURE_THRESHOLD`
- `CB_RESET_TIMEOUT_SEC`
- `CB_HALF_OPEN_MAX_PROBES`
- `CB_HALF_OPEN_SUCCESS_THRESHOLD`
- `ROUTING_INFLIGHT_WEIGHT`
- `ROUTING_LATENCY_WEIGHT`
- `ROUTING_ERROR_WEIGHT`
- `ROUTING_EXPLORATION_RATE`
- `ROUTING_MAX_IDLE_MS`

## Routing and Reliability

- Routing score: `inflight * W1 + latency * W2 + error_rate * W3`
- Hybrid routing controls:
  - exploration sampling (`ROUTING_EXPLORATION_RATE`)
  - idle rebalance (`ROUTING_MAX_IDLE_MS`)
- Circuit breaker:
  - open after consecutive failures
  - half-open probe recovery (`CB_HALF_OPEN_MAX_PROBES`, `CB_HALF_OPEN_SUCCESS_THRESHOLD`)

## Related Docs

- `docs/design.md`
- `docs/status-codes.md`
- `docs/request-response-and-logging.md`
- `docs/conversation-id.md`
- `docs/smoke-test.md`
- `docs/run-locally.md`
- `docs/docker.md`

## Quick Start

Many systems only expose `python3` / `pip3`, or have no `python`/`pip` on `PATH`. If you see `command not found: python` or `command not found: pip`, use a virtual environment and the `python3` launcher:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m app.main
```

Verify service health:

```bash
curl -sS http://127.0.0.1:30182/health
curl -sS http://127.0.0.1:30182/ready
```

Then run full endpoint checks via `docs/smoke-test.md`.

If `python3` is missing, install Python 3.12+ (for example on macOS: `brew install python`).

## Examples and Smoke Tests

Full curl coverage (health, ready, rerank, header propagation, and negative tests) is in `docs/smoke-test.md`.

Header-based rerank example:

```bash
export GW_URL="http://127.0.0.1:30182"
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
