from __future__ import annotations

import logging
import os


def configure_logging() -> None:
    """
    Logging simple y estable. Respeta LOG_LEVEL si existe en .env.mongo_extractor.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
