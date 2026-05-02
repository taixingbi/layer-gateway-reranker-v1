import time

from app.core.config import BackendConfig, CircuitBreakerConfig, RoutingConfig
from app.routing.selector import BackendSelector


class StubRandom:
    def random(self) -> float:
        return 0.0

    def choice(self, items):
        return items[-1]


def test_routing_prefers_lower_score():
    selector = BackendSelector(
        backends=(
            BackendConfig(name="a", url="http://a"),
            BackendConfig(name="b", url="http://b"),
        ),
        routing=RoutingConfig(inflight_weight=10.0, latency_weight=1.0, error_weight=100.0, exploration_rate=0.0),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=2, reset_timeout_sec=30),
    )

    selector.mark_result("a", latency_ms=200, success=True)
    selector.mark_result("b", latency_ms=30, success=True)
    assert selector.pick().name == "b"


def test_failures_do_not_poison_latency_ewma():
    selector = BackendSelector(
        backends=(
            BackendConfig(name="a", url="http://a"),
            BackendConfig(name="b", url="http://b"),
        ),
        routing=RoutingConfig(inflight_weight=10.0, latency_weight=1.0, error_weight=100.0, exploration_rate=0.0),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=100, reset_timeout_sec=30),
    )
    for _ in range(5):
        selector.mark_result("a", latency_ms=30.0, success=True)
    selector.mark_result("a", latency_ms=15000.0, success=False)
    assert selector.state["a"].latency_ms == 30.0


def test_circuit_breaker_opens_after_threshold():
    selector = BackendSelector(
        backends=(BackendConfig(name="a", url="http://a"),),
        routing=RoutingConfig(exploration_rate=0.0),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=2, reset_timeout_sec=30),
    )

    selector.mark_start("a")
    selector.mark_result("a", latency_ms=100, success=False)
    selector.mark_start("a")
    selector.mark_result("a", latency_ms=100, success=False)
    assert selector.pick() is None


def test_exploration_can_pick_non_best_backend():
    selector = BackendSelector(
        backends=(
            BackendConfig(name="a", url="http://a"),
            BackendConfig(name="b", url="http://b"),
        ),
        routing=RoutingConfig(
            inflight_weight=10.0,
            latency_weight=1.0,
            error_weight=100.0,
            exploration_rate=1.0,
            max_idle_ms=0,
        ),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=2, reset_timeout_sec=30),
        rng=StubRandom(),
    )
    selector.mark_result("a", latency_ms=10, success=True)
    selector.mark_result("b", latency_ms=200, success=True)

    picked = selector.pick()
    assert picked is not None
    assert picked.name == "b"
    assert selector.last_pick_reason == "exploration"


def test_idle_backend_gets_rebalanced_after_threshold():
    selector = BackendSelector(
        backends=(
            BackendConfig(name="a", url="http://a"),
            BackendConfig(name="b", url="http://b"),
        ),
        routing=RoutingConfig(
            inflight_weight=10.0,
            latency_weight=1.0,
            error_weight=100.0,
            exploration_rate=0.0,
            max_idle_ms=10,
        ),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=2, reset_timeout_sec=30),
    )

    now = selector.state["a"].last_selected_at
    selector.state["a"].last_selected_at = now
    selector.state["b"].last_selected_at = now - 1.0

    picked = selector.pick()
    assert picked is not None
    assert picked.name == "b"
    assert selector.last_pick_reason == "idle_rebalance"


def test_exploration_respects_health_and_exclusions():
    selector = BackendSelector(
        backends=(
            BackendConfig(name="a", url="http://a"),
            BackendConfig(name="b", url="http://b"),
        ),
        routing=RoutingConfig(exploration_rate=1.0, max_idle_ms=0),
        circuit_breaker=CircuitBreakerConfig(failure_threshold=1, reset_timeout_sec=30),
        rng=StubRandom(),
    )

    selector.mark_start("b")
    selector.mark_result("b", latency_ms=100, success=False)
    picked = selector.pick(excluded={"a"})
    assert picked is None


def test_half_open_allows_limited_probes_and_closes_on_success():
    selector = BackendSelector(
        backends=(BackendConfig(name="a", url="http://a"),),
        routing=RoutingConfig(exploration_rate=0.0, max_idle_ms=0),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=1,
            reset_timeout_sec=30,
            half_open_max_probes=1,
            half_open_success_threshold=1,
        ),
    )

    selector.mark_start("a")
    selector.mark_result("a", latency_ms=50, success=False)
    state = selector.state["a"]
    state.circuit_open_until = time.time() - 1.0

    picked = selector.pick()
    assert picked is not None
    assert picked.name == "a"
    assert state.circuit_half_open is True

    selector.mark_start("a")
    assert state.half_open_inflight == 1
    assert selector.pick() is None

    selector.mark_result("a", latency_ms=20, success=True)
    assert state.circuit_half_open is False
    assert state.circuit_open_until == 0.0
    assert state.consecutive_failures == 0


def test_half_open_failure_reopens_immediately():
    selector = BackendSelector(
        backends=(BackendConfig(name="a", url="http://a"),),
        routing=RoutingConfig(exploration_rate=0.0, max_idle_ms=0),
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=1,
            reset_timeout_sec=30,
            half_open_max_probes=1,
            half_open_success_threshold=2,
        ),
    )

    selector.mark_start("a")
    selector.mark_result("a", latency_ms=50, success=False)
    state = selector.state["a"]
    state.circuit_open_until = time.time() - 1.0

    assert selector.pick() is not None
    selector.mark_start("a")
    selector.mark_result("a", latency_ms=50, success=False)

    assert state.circuit_half_open is False
    assert state.circuit_open() is True
