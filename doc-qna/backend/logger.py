import logging
import os
import sys
from datetime import datetime
from typing import Optional, TextIO

# Visual separators for full pipeline runs in log files / console.
PROCESS_BOUNDARY_LEN = 80
PROCESS_BOUNDARY_CHAR = "="


def log_process_start(
    logger: logging.Logger,
    process_name: str,
    *,
    extra: str = "",
) -> None:
    """Log a clear block marking the start of one end-to-end process."""
    bar = PROCESS_BOUNDARY_CHAR * PROCESS_BOUNDARY_LEN
    suffix = f" | {extra}" if extra.strip() else ""
    logger.info("")
    logger.info(bar)
    logger.info("PROCESS START >>> %s%s", process_name, suffix)
    logger.info(bar)
    logger.info("")


def log_process_end(
    logger: logging.Logger,
    process_name: str,
    *,
    extra: str = "",
) -> None:
    """Log a clear block marking the end of one end-to-end process."""
    bar = PROCESS_BOUNDARY_CHAR * PROCESS_BOUNDARY_LEN
    suffix = f" | {extra}" if extra.strip() else ""
    logger.info("")
    logger.info(bar)
    logger.info("PROCESS END <<< %s%s", process_name, suffix)
    logger.info(bar)
    logger.info("")


class _DailyDateFileHandler(logging.FileHandler):
    """
    File handler that writes to data/logs/YYYY-MM-DD.log and automatically switches at midnight
    (based on local time) without requiring an app restart.
    """

    def __init__(self, log_dir: str, *, encoding: str = "utf-8") -> None:
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self._current_date = datetime.now().strftime("%Y-%m-%d")
        filename = os.path.join(self.log_dir, f"{self._current_date}.log")
        super().__init__(filename, mode="a", encoding=encoding, delay=True)

    def emit(self, record: logging.LogRecord) -> None:
        d = datetime.now().strftime("%Y-%m-%d")
        if d != self._current_date:
            self._current_date = d
            self.close()
            self.baseFilename = os.path.abspath(os.path.join(self.log_dir, f"{self._current_date}.log"))
        super().emit(record)


def get_logger(
    name: str = "yuktra_qna",
    *,
    level: int = logging.INFO,
    log_dir: str = "data/logs",
    also_console: bool = True,
    console_stream: Optional[TextIO] = None,
) -> logging.Logger:
    """
    Returns a logger that writes to a date-based file in logs/.
    Each day gets a separate log file: data/logs/YYYY-MM-DD.log.
    Use ``console_stream=sys.stdout`` when stderr is shared with tqdm/PDF tools.
    """
    logger = logging.getLogger(name)
    if getattr(logger, "_daily_configured", False):
        return logger

    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_h = _DailyDateFileHandler(log_dir)
    file_h.setLevel(level)
    file_h.setFormatter(fmt)
    logger.addHandler(file_h)

    if also_console:
        stream = console_stream if console_stream is not None else sys.stderr
        sh = logging.StreamHandler(stream=stream)
        sh.setLevel(level)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    logger._daily_configured = True  # type: ignore[attr-defined]
    return logger