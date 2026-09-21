import os
import sys
import logging
from app import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("audit_system.production")

PORT = int(os.environ.get("PORT", 5000))
HOST = os.environ.get("HOST", "0.0.0.0")
THREADS = int(os.environ.get("THREADS", 16))

if __name__ == "__main__":
    banner = f"""
===================================================================
       SUPERMARKET POS & AUDIT SYSTEM - PRODUCTION SERVER
===================================================================
  Mode:       Production Multi-Threaded WSGI (Waitress)
  Host:       {HOST}:{PORT}
  Threads:    {THREADS} concurrent worker threads
  Storage:    SQLite with Write-Ahead Logging (WAL)
  Cache:      L1 In-Memory LRU Cache (<0.2ms)
  Resilience: Circuit Breakers & Global Safe Boundaries

  Endpoints:
    Cashier POS:         http://localhost:{PORT}/
    Admin & Audit Hub:   http://localhost:{PORT}/admin
    Liveness Probe:      http://localhost:{PORT}/healthz
    Readiness Probe:     http://localhost:{PORT}/readyz
===================================================================
    """
    print(banner)

    try:
        from waitress import serve
        logger.info(f"Serving application with Waitress ({THREADS} threads on {HOST}:{PORT})...")
        serve(
            app,
            host=HOST,
            port=PORT,
            threads=THREADS,
            connection_limit=1000,
            channel_timeout=30,
            asyncore_use_poll=True
        )
    except ImportError:
        logger.warning("Waitress not found. Falling back to threaded WSGI development server...")
        app.run(host=HOST, port=PORT, threaded=True, debug=False)
