"""Entrypoint: run the FastAPI app (which also starts the SMTP server)."""

import logging

import uvicorn

from . import config


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("main")
    log.info("Starting Free SMTP Test Server")
    log.info("  Web UI / API : http://%s:%s", config.HTTP_HOST, config.HTTP_PORT)
    log.info(
        "  SMTP capture : %s:%s (no auth) — public host: %s",
        config.SMTP_HOST, config.SMTP_PORT, config.PUBLIC_SMTP_HOST,
    )
    uvicorn.run(
        "app.api:app",
        host=config.HTTP_HOST,
        port=config.HTTP_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
