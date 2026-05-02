# layer-gateway-reranker-v1

Request-level routing gateway for `/v1/rerank` across multiple vLLM backends.

## Endpoints

- `POST /v1/rerank`
- `GET /health`
- `GET /metrics`

## Correlation headers (recommended)

- `X-Request-Id`
- `X-Trace-Id`
- `X-Session-Id`

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
- `docs/run-locally.md`
- `docs/docker.md`

## Run

Many systems only expose `python3` / `pip3`, or have no `python`/`pip` on `PATH`. If you see `command not found: python` or `command not found: pip`, use a virtual environment and the `python3` launcher:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m app.main
```

Without activating the venv:

```bash
cd /path/to/layer-gateway-reranker-v1
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python -m app.main
```

If `python3` is missing, install Python 3.12+ (for example on macOS: `brew install python`).

## Test after deploy (k3s)

OpenAPI / Swagger UI: [http://192.168.86.179:30182/docs](http://192.168.86.179:30182/docs)

```bash
## simple
curl -X POST http://192.168.86.179:30182/v1/rerank \
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

```bash
## header with request-id, trace-id and session id
curl -X POST http://192.168.86.179:30182/v1/rerank \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: request_id_1" \
  -H "X-Trace-Id: trace_id_1" \
  -H "X-Session-Id: session_id_1" \
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
