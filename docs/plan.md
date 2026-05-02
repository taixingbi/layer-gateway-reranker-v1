layer-gateway-reranker-v1

A lightweight, request-level routing gateway for reranker workloads across multiple vLLM backends.

This service complements
layer-gateway-inference-v1 and layer-gateway-embed-v1 by handling /v1/rerank traffic independently.

Overview

layer-gateway-reranker-v1 provides:

Load-aware routing across rerank backends
Request-level scheduling (not connection-level)
Retry and circuit breaker for reliability
Structured logging and metrics
Simple, production-ready architecture
Why a Separate Gateway?

Rerank workloads differ significantly from chat/inference:

High QPS, short requests with many candidate documents per call
No streaming / TTFT concerns
Different latency and load characteristics

Separating gateways ensures:

Clean routing logic
Independent scaling and deployment
No interference between chat, embedding, and rerank workloads
API
POST /v1/rerank

Compatible with vLLM-style rerank API.

Example
curl -X POST http://<gateway-host>:30182/v1/rerank \
  -H "X-Request-Id: request_id_1" \
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
GET /health

Returns service health.

GET /metrics

Prometheus-compatible metrics.

Architecture
Client
   ↓
Reranker Gateway (/v1/rerank)
   ↓
Routing / Scheduler (load-aware)
   ↓
vLLM Reranker Backends
Backends

Example configuration:

backends:
  - name: rerank-node-1
    url: http://192.168.86.173:8002
  - name: rerank-node-2
    url: http://192.168.86.176:8002

Each backend must expose:

POST /v1/rerank
Routing Strategy

The gateway selects the best backend using a score-based approach:

score = inflight * W1 + latency * W2 + error_rate * W3

Where:

inflight = current active requests
latency = recent response time
error_rate = recent failures

Lowest score wins.

Request Classification

Requests are classified for observability and future tuning:

SMALL_RERANK
MEDIUM_RERANK
LARGE_RERANK

Based on:

number of candidate documents
total character size (query + documents)
Reliability
Retry

Retries occur on:

timeouts
502, 503, 504

Config:

retry:
  max_attempts: 2

Retries will prefer a different backend.

Circuit Breaker

Each backend is protected by a circuit breaker:

Opens after repeated failures
Temporarily removes backend from routing
Automatically retries after cooldown
Configuration

Example:

server:
  host: 0.0.0.0
  port: 30182

timeouts:
  connect_ms: 1000
  read_ms: 15000

retry:
  max_attempts: 2
  retryable_statuses: [502, 503, 504]

circuit_breaker:
  failure_threshold: 5
  reset_timeout_sec: 30

routing:
  inflight_weight: 10.0
  latency_weight: 1.0
  error_weight: 100.0

backends:
  - name: rerank-node-1
    url: http://192.168.86.173:8002
  - name: rerank-node-2
    url: http://192.168.86.176:8002
Security

All requests must include:

X-Request-Id
X-Session-Id

Metrics

Prometheus metrics include:

gateway_rerank_requests_total
gateway_rerank_request_latency_ms
gateway_rerank_backend_selected_total
gateway_rerank_inflight
gateway_rerank_failures_total
gateway_rerank_retries_total
Logging

Structured logs include:

request lifecycle events
backend selection
retry attempts
failures and circuit breaker events

Fields:

request_id
session_id
backend
latency_ms
model
input size
request class
Repository Structure
app/
  api/
  core/
  routing/
  proxy/
  models/
  metrics/

config/
tests/
Dockerfile
README.md
Development Plan
Phase 1 — Bootstrap
Create repo
Copy reusable components from embed gateway
Remove embedding-specific logic
Phase 2 — Core Endpoint
Implement /v1/rerank
Add config + security
Add backend routing
Phase 3 — Reliability
Retry logic
Circuit breaker
Backend health tracking
Phase 4 — Observability
Logging
Metrics
/health
Phase 5 — Deployment
Build Docker image
Run locally or in cluster
Phase 6 — Load Testing
Concurrency tests
Failover tests
Latency validation
Design Principles
Request-level routing (not connection-level)
Keep gateway simple — no batching
Let vLLM handle batching
Fail fast, retry smart
Prefer stability over complexity
Future Enhancements (Optional)
Admission queue / rate limiting
Dynamic backend discovery
Adaptive routing weights
Request batching (if needed)
Status

Initial version (v1) focuses on:

Stable routing
Reliability
Observability
Simple deployment
