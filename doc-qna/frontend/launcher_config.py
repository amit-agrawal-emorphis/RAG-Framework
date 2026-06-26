"""Launcher settings: read from environment with defaults."""

import os
from typing import Literal, Optional

LaunchMode = Literal["py", "bin"]

_DEFAULT_LAUNCH_MODE: LaunchMode = "py"
_DEFAULT_BIN_NAME = "yuktra-ipc.bin"


def _normalize_mode(raw: Optional[str]) -> LaunchMode:
    if not raw:
        return _DEFAULT_LAUNCH_MODE
    v = raw.strip().lower()
    if v in ("py", "python"):
        return "py"
    if v in ("bin", "binary", "exe"):
        return "bin"
    return _DEFAULT_LAUNCH_MODE


def get_launch_mode() -> LaunchMode:
    return _normalize_mode(os.environ.get("YUKTRA_IPC_LAUNCH_MODE"))


def get_bin_name() -> str:
    v = os.environ.get("YUKTRA_IPC_BIN_NAME", "").strip()
    return v or _DEFAULT_BIN_NAME
