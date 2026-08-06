"""파일+stdout 동시 출력 로거 설정."""
from __future__ import annotations

import logging
from pathlib import Path


def setup_logging(output_root: Path, name: str = "step4") -> logging.Logger:
    log_dir = output_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    file_handler = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    return logger
