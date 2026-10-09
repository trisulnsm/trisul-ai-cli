"""Optional runtime overrides used by the Docker image.

When the matching environment variable is unset, every helper returns the
same path the pip install has always used. Pip CLI mode and pip API mode
are unchanged unless an operator sets one of these variables.
"""

import logging
import os
from pathlib import Path


def package_dir() -> Path:
    return Path(__file__).resolve().parent


def env_file_path(default_dir: Path | None = None) -> Path:
    override = os.environ.get("TRISUL_AI_ENV_FILE", "").strip()
    if override:
        return Path(override)
    base = package_dir() if default_dir is None else default_dir
    return base / ".env"


def chroma_store_path(default_dir: Path | None = None) -> Path:
    override = os.environ.get("TRISUL_AI_CHROMA_DIR", "").strip()
    if override:
        return Path(override)
    base = package_dir() if default_dir is None else default_dir
    return base / "chroma_store"


def configure_logging() -> Path:
    """Log to trisul_ai_cli.log in the working directory, as before.

    TRISUL_AI_LOG_DIR redirects the file. TRISUL_AI_LOG_STDOUT=1 also copies
    each line to stderr so `docker logs` shows the same file.
    """
    log_dir = os.environ.get("TRISUL_AI_LOG_DIR", "").strip()
    if log_dir:
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        log_file = directory / "trisul_ai_cli.log"
    else:
        log_file = Path.cwd() / "trisul_ai_cli.log"

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8")
    ]
    flag = os.environ.get("TRISUL_AI_LOG_STDOUT", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=handlers,
        force=True,
    )
    return log_file


def uvicorn_log_config(log_file: Path) -> dict:
    """Uvicorn's own loggers do not propagate to the root file handler.

    Copy their stream output into the same trisul_ai_cli.log the rest of the
    process uses, so the file inside the container matches `docker logs`.
    """
    import copy

    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config["formatters"]["file"] = {
        "format": "%(asctime)s - %(levelname)s - %(message)s",
    }
    config["handlers"]["file"] = {
        "class": "logging.FileHandler",
        "filename": str(log_file),
        "encoding": "utf-8",
        "formatter": "file",
    }
    for name in ("uvicorn", "uvicorn.access"):
        handlers = config["loggers"][name].setdefault("handlers", [])
        if "file" not in handlers:
            handlers.append("file")
    return config
