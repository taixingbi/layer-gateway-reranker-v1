"""Backend routing: score-based pick, hybrid exploration, circuit breaker with half-open."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict

from app.core.config import BackendConfig, CircuitBreakerConfig, RoutingConfig


@dataclass
class BackendState:
    """Per-backend counters for routing score and circuit breaker."""

    inflight: int = 0
    latency_ms: float = 0.0
    errors: int = 0
    requests: int = 0
    consecutive_failures: int = 0
    circuit_open_until: float = 0.0
    circuit_half_open: bool = False
    half_open_inflight: int = 0
    half_open_successes: int = 0
    last_selected_at: float = field(default_factory=time.time)

    def error_rate(self) -> float:
        """Errors divided by completed attempts (includes successes and failures)."""
        if self.requests == 0:
            return 0.0
        return self.errors / self.requests

    def circuit_open(self) -> bool:
        """True during cool-down after failures (blocks normal picks until half-open)."""
        return time.time() < self.circuit_open_until


class BackendSelector:
    """Choose upstream backends using score, idle rebalance, exploration, and breaker state."""

    def __init__(
        self,
        backends: tuple[BackendConfig, ...],
        routing: RoutingConfig,
        circuit_breaker: CircuitBreakerConfig,
        rng: random.Random | None = None,
    ) -> None:
        self.backends = backends
        self.routing = routing
        self.circuit_breaker = circuit_breaker
        self.rng = rng or random.Random()
        self.state: Dict[str, BackendState] = {b.name: BackendState() for b in backends}
        self.last_pick_reason = "score"

    def _open_circuit(self, s: BackendState) -> None:
        """Open the circuit and clear half-open probe counters."""
        s.circuit_open_until = time.time() + self.circuit_breaker.reset_timeout_sec
        s.circuit_half_open = False
        s.half_open_inflight = 0
        s.half_open_successes = 0

    def _close_circuit(self, s: BackendState) -> None:
        """Close the circuit: backend is fully eligible again."""
        s.circuit_open_until = 0.0
        s.circuit_half_open = False
        s.half_open_inflight = 0
        s.half_open_successes = 0

    def _score(self, backend_name: str) -> float:
        """Routing score (lower is better): inflight + success EWMA latency + error rate."""
        state = self.state[backend_name]
        return (
            (state.inflight * self.routing.inflight_weight)
            + (state.latency_ms * self.routing.latency_weight)
            + (state.error_rate() * self.routing.error_weight)
        )

    def _eligible_backends(self, excluded_names: set[str]) -> list[BackendConfig]:
        """
        Select backends eligible for the next request.

        Eligibility rules:
        - Exclude backends in `excluded_names`
        - Skip backends with an OPEN circuit
        - Transition OPEN → HALF_OPEN after cooldown expires
        - In HALF_OPEN, allow only limited probe requests

        Behavior notes:
        - This function mutates backend state (OPEN → HALF_OPEN transition)
        - HALF_OPEN backends are rate-limited to avoid overload during recovery
        """
        eligible: list[BackendConfig] = []
        now = time.time()
        for backend in self.backends:
            if backend.name in excluded_names:
                continue
            state = self.state[backend.name]
            if state.circuit_open():
                continue
            if state.circuit_open_until > 0 and not state.circuit_half_open and now >= state.circuit_open_until:
                state.circuit_half_open = True
                state.half_open_inflight = 0
                state.half_open_successes = 0
            if state.circuit_half_open and state.half_open_inflight >= self.circuit_breaker.half_open_max_probes:
                continue
            eligible.append(backend)
        return eligible

    def _pick_idle_backend(self, eligible: list[BackendConfig], now: float) -> BackendConfig | None:
        """If a backend has not been picked for `max_idle_ms`, prefer the longest-idle one."""
        if self.routing.max_idle_ms <= 0:
            return None
        max_idle_sec = self.routing.max_idle_ms / 1000.0
        stale = [backend for backend in eligible if (now - self.state[backend.name].last_selected_at) >= max_idle_sec]
        if not stale:
            return None
        return max(stale, key=lambda b: now - self.state[b.name].last_selected_at)

    def _pick_best_score(self, eligible: list[BackendConfig]) -> BackendConfig:
        """Pick the eligible backend with the lowest `_score`."""
        return min(eligible, key=lambda backend: self._score(backend.name))

    def _pick_exploration(self, eligible: list[BackendConfig]) -> BackendConfig:
        """Random eligible backend (exploration traffic for fresh latency samples)."""
        return self.rng.choice(eligible)

    def pick(self, excluded: set[str] | None = None) -> BackendConfig | None:
        """
        Pick a backend for the next upstream call.

        Priority:
        1. Idle rebalance (anti-starvation)
        2. Exploration sample (random eligible backend)
        3. Lowest routing score

        `excluded` skips backends (retries add failed backends here).
        Sets `last_pick_reason` for `routing_pick` logs.
        """
        excluded_names = excluded or set()
        eligible = self._eligible_backends(excluded_names)
        if not eligible:
            self.last_pick_reason = "none"
            return None

        now = time.time()
        idle_pick = self._pick_idle_backend(eligible, now)
        if idle_pick is not None:
            self.state[idle_pick.name].last_selected_at = now
            self.last_pick_reason = "idle_rebalance"
            return idle_pick

        if len(eligible) > 1 and self.routing.exploration_rate > 0 and self.rng.random() < self.routing.exploration_rate:
            backend = self._pick_exploration(eligible)
            self.state[backend.name].last_selected_at = now
            self.last_pick_reason = "exploration"
            return backend

        backend = self._pick_best_score(eligible)
        self.state[backend.name].last_selected_at = now
        self.last_pick_reason = "score"
        return backend

    def mark_start(self, backend_name: str) -> None:
        """Increment `inflight` and half-open probe counter when a call starts."""
        s = self.state[backend_name]
        s.inflight += 1
        if s.circuit_half_open:
            s.half_open_inflight += 1

    def mark_result(self, backend_name: str, latency_ms: float, success: bool) -> None:
        """
        Record completion: decrement inflight, update EWMA latency on success only, update breaker.

        On success, EWMA is `0.2 * old + 0.8 * new`. Failures increment errors and may open the circuit;
        half-open failures reopen immediately.
        """
        s = self.state[backend_name]
        s.inflight = max(0, s.inflight - 1)
        if s.circuit_half_open:
            s.half_open_inflight = max(0, s.half_open_inflight - 1)
        s.requests += 1
        if success:
            s.latency_ms = latency_ms if s.latency_ms == 0 else (s.latency_ms * 0.2) + (latency_ms * 0.8)
            if s.circuit_half_open:
                s.half_open_successes += 1
                if s.half_open_successes >= self.circuit_breaker.half_open_success_threshold:
                    s.consecutive_failures = 0
                    self._close_circuit(s)
            else:
                s.consecutive_failures = 0
                self._close_circuit(s)
            return
        s.errors += 1
        s.consecutive_failures += 1
        if s.circuit_half_open or s.consecutive_failures >= self.circuit_breaker.failure_threshold:
            self._open_circuit(s)
