"""
Resilience primitives used by every outbound dependency
(Kubernetes API, Prometheus, LLM provider).

    retry_call      - bounded retries, exponential backoff + full jitter,
                      only for errors that are actually retryable,
                      never past an overall deadline.
    CircuitBreaker  - stop hammering a dependency that is already down;
                      fail fast so the investigation can degrade
                      gracefully instead of hanging.
    TokenBucket     - client-side rate limiting so the agent cannot
                      become the incident (e.g. flood the API server
                      while it is already struggling).

No third-party dependency on purpose: the behaviour is small, easy
to unit test, and easy to explain in a design review.
"""

import logging
import random
import threading
import time

logger = logging.getLogger(__name__)


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""


class RateLimitedError(Exception):
    """Raised when a call cannot acquire a rate-limit token in time."""


class CircuitBreaker:
    """
    Classic three-state circuit breaker.

        CLOSED     calls pass through; consecutive failures are counted
        OPEN       calls fail fast with CircuitOpenError
        HALF_OPEN  after `reset_timeout` one trial call is allowed;
                   success closes the circuit, failure re-opens it
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        name,
        failure_threshold=5,
        reset_timeout=30.0,
        clock=time.monotonic,
        is_failure=None,
    ):
        self.name = name
        # Only dependency faults should trip the breaker. A 404 or a
        # rejected query means the dependency is up and answering.
        self.is_failure = is_failure or (lambda exc: True)
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

    @property
    def state(self):
        with self._lock:
            self._maybe_half_open()
            return self._state

    def _maybe_half_open(self):
        if (
            self._state == self.OPEN
            and self._clock() - self._opened_at >= self.reset_timeout
        ):
            self._state = self.HALF_OPEN
            self._trial_in_flight = False

    def before_call(self):
        with self._lock:
            self._maybe_half_open()

            if self._state == self.OPEN:
                raise CircuitOpenError(
                    f"circuit '{self.name}' is open; failing fast"
                )

            if self._state == self.HALF_OPEN:
                if self._trial_in_flight:
                    raise CircuitOpenError(
                        f"circuit '{self.name}' is half-open; "
                        "trial call already in flight"
                    )
                self._trial_in_flight = True

    def record_success(self):
        with self._lock:
            self._state = self.CLOSED
            self._failures = 0
            self._trial_in_flight = False

    def record_failure(self):
        with self._lock:
            self._failures += 1
            self._trial_in_flight = False

            if (
                self._state == self.HALF_OPEN
                or self._failures >= self.failure_threshold
            ):
                if self._state != self.OPEN:
                    logger.warning(
                        "circuit opened",
                        extra={"circuit": self.name,
                               "failures": self._failures},
                    )
                self._state = self.OPEN
                self._opened_at = self._clock()

    def call(self, func, *args, **kwargs):
        self.before_call()

        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            if self.is_failure(exc):
                self.record_failure()
            else:
                self.record_success()
            raise

        self.record_success()
        return result


class TokenBucket:
    """
    Thread-safe token bucket.

    `rate` tokens are added per second up to `capacity`. acquire()
    blocks up to `timeout` seconds and raises RateLimitedError rather
    than queueing forever.
    """

    def __init__(self, rate, capacity, clock=time.monotonic, sleep=time.sleep):
        self.rate = float(rate)
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self):
        now = self._clock()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)

    def try_acquire(self):
        with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False

    def acquire(self, timeout=5.0):
        deadline = self._clock() + timeout

        while True:
            if self.try_acquire():
                return

            if self._clock() >= deadline:
                raise RateLimitedError("rate limit exceeded")

            self._sleep(min(1.0 / self.rate, 0.25))


def retry_call(
    func,
    *,
    is_retryable,
    attempts=3,
    base_delay=0.5,
    max_delay=8.0,
    deadline=None,
    sleep=time.sleep,
    clock=time.monotonic,
    breaker=None,
    operation="call",
):
    """
    Call `func()` with bounded retries.

    - Only errors where `is_retryable(exc)` is True are retried; a 404
      or 403 is an answer, not a transient fault.
    - Full-jitter exponential backoff avoids synchronized retry storms.
    - `deadline` (absolute monotonic time) caps total wall-clock time.
    - CircuitOpenError is never retried: the breaker already decided.
    """

    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            if breaker is not None:
                return breaker.call(func)
            return func()

        except CircuitOpenError:
            raise

        except Exception as exc:
            last_exc = exc

            if not is_retryable(exc) or attempt == attempts:
                raise

            delay = random.uniform(0, min(max_delay, base_delay * 2 ** (attempt - 1)))

            if deadline is not None and clock() + delay >= deadline:
                raise

            logger.info(
                "retrying after transient error",
                extra={
                    "operation": operation,
                    "attempt": attempt,
                    "delay_s": round(delay, 3),
                    "error": type(exc).__name__,
                },
            )
            sleep(delay)

    raise last_exc  # pragma: no cover - loop always returns or raises
