import logging
import sys


def configure_logging(debug: bool = False) -> None:
    """Configure structured JSON-style logging for the application."""
    level = logging.DEBUG if debug else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    fmt = logging.Formatter(
        '{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING if not debug else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
