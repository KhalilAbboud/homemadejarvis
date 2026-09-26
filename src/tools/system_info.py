"""Read-only system information helpers."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
from pathlib import Path
from typing import Any


def _memory_bytes() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return int(status.total_physical)
    return None


async def get_system_specs() -> dict[str, Any]:
    """Return non-sensitive, read-only operating-system specifications."""
    root = Path.cwd().anchor or "C:\\"
    try:
        disk = shutil.disk_usage(root)
        disk_info: dict[str, int] | None = {"total_bytes": disk.total, "free_bytes": disk.free}
    except OSError:
        disk_info = None

    return {
        "operating_system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "memory_total_bytes": _memory_bytes(),
        "system_disk": disk_info,
    }