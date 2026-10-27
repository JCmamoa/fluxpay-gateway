"""Entrypoint: ``python -m app`` runs the API + worker + dispatcher in one process."""

from __future__ import annotations

import uvicorn

from .main import app


def main() -> None:
    settings = app.state.settings
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
