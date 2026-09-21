import time
import threading
import logging
from collections import OrderedDict
from functools import wraps

logger = logging.getLogger("audit_system.resilience")


class LRUCacheTTL:
    """Thread-safe in-memory LRU cache with TTL expiration."""

    def __init__(self, maxsize=2000, default_ttl=60.0):
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key):
        with self.lock:
            if key not in self.cache:
                return None
            val, expiry = self.cache[key]
            if time.time() > expiry:
                del self.cache[key]
                return None
            # Move to end (recently used)
            self.cache.move_to_end(key)
            return val

    def set(self, key, value, ttl=None):
        ttl = ttl if ttl is not None else self.default_ttl
        expiry = time.time() + ttl
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = (value, expiry)
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

    def delete(self, key):
        with self.lock:
            if key in self.cache:
                del self.cache[key]

    def clear(self):
        with self.lock:
            self.cache.clear()


class CircuitBreakerOpenException(Exception):
    """Raised when circuit breaker is in OPEN state."""
    pass


class CircuitBreaker:
    """Stateful Circuit Breaker preventing cascading failure across subsystems."""

    def __init__(self, name="DefaultCircuit", failure_threshold=5, recovery_time=10.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.failure_count = 0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.last_state_change = time.time()
        self.lock = threading.Lock()

    def call(self, fn, *args, **kwargs):
        with self.lock:
            now = time.time()
            if self.state == "OPEN":
                if now - self.last_state_change > self.recovery_time:
                    self.state = "HALF_OPEN"
                    self.last_state_change = now
                    logger.info(f"[CIRCUIT BREAKER {self.name}] Transitioned to HALF_OPEN (probing recovery).")
                else:
                    raise CircuitBreakerOpenException(f"Circuit Breaker '{self.name}' is OPEN. Requests blocked to preserve system stability.")

        try:
            result = fn(*args, **kwargs)
            with self.lock:
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                    self.failure_count = 0
                    self.last_state_change = time.time()
                    logger.info(f"[CIRCUIT BREAKER {self.name}] Service recovered. State is now CLOSED.")
                elif self.state == "CLOSED":
                    self.failure_count = 0
            return result
        except Exception as e:
            with self.lock:
                self.failure_count += 1
                if self.failure_count >= self.failure_threshold:
                    self.state = "OPEN"
                    self.last_state_change = time.time()
                    logger.error(f"[CIRCUIT BREAKER {self.name}] Tripped to OPEN! Failures: {self.failure_count}. Error: {e}")
            raise


# Global instances
product_cache = LRUCacheTTL(maxsize=1000, default_ttl=120.0)
metrics_cache = LRUCacheTTL(maxsize=100, default_ttl=10.0)
db_circuit_breaker = CircuitBreaker("DatabaseCircuit", failure_threshold=5, recovery_time=8.0)


def safe_boundary(fallback_value=None):
    """Decorator to ensure functions never crash the application."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error caught by safe_boundary in '{fn.__name__}': {e}", exc_info=True)
                return fallback_value
        return wrapper
    return decorator
