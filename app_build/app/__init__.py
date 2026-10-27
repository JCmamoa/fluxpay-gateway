"""FluxPay — Simplified Payment Gateway.

A production-minded, single-process demo payment gateway built with
FastAPI + SQLite.  The package hosts three logical components that can run
inside one process for local use:

* the HTTP API (stateless),
* the payment worker (durable queue consumer),
* the webhook dispatcher (HMAC signing + retries + DLQ).
"""

__version__ = "1.0.0"
