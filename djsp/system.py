from __future__ import annotations

import datetime
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


def total_memory_mb() -> Optional[float]:
    try:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 20
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / 2 ** 20
    except Exception:
        pass
    return None


def cpu_model() -> str:
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    if os.name == "nt":
        try:
            import winreg
            processor = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, processor) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def peak_rss_mb() -> Optional[float]:
    try:
        import resource
        scale = 1 if sys.platform == "darwin" else 1024
        own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        children = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        return max(own, children) * scale / 2 ** 20
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            kernel32 = ctypes.WinDLL("kernel32")
            psapi = ctypes.WinDLL("psapi")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return counters.PeakWorkingSetSize / 2 ** 20
        except Exception:
            pass
    return None


def physical_cores() -> List[List[int]]:
    try:
        usable = sorted(os.sched_getaffinity(0))
    except AttributeError:
        return []
    groups: Dict[tuple, List[int]] = {}
    for cpu in usable:
        topology = Path("/sys/devices/system/cpu") / f"cpu{cpu}" / "topology"
        try:
            key = (int((topology / "physical_package_id").read_text()), int((topology / "core_id").read_text()))
        except (OSError, ValueError):
            key = (-1, cpu)
        groups.setdefault(key, []).append(cpu)
    return sorted(groups.values(), key=min)


def pin_to(cpus: List[int]) -> bool:
    try:
        os.sched_setaffinity(0, set(cpus))
        return True
    except (AttributeError, OSError):
        return False


def limit_memory(megabytes: Optional[float]) -> bool:
    if not megabytes:
        return False
    try:
        import resource
    except ImportError:
        return False
    limit = int(megabytes * 2 ** 20)
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return True


def package_version(name: str) -> Optional[str]:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None


def git_revision(root: Path) -> Dict[str, object]:
    def git(*arguments: str) -> Optional[str]:
        try:
            completed = subprocess.run(["git", *arguments], cwd=root, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() if completed.returncode == 0 else None

    status = git("status", "--porcelain", "--untracked-files=no")
    return {"commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(status) if status is not None else None}


def run_metadata(root: Path, settings: Dict[str, object]) -> Dict[str, object]:
    return {
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "command": sys.argv,
        "host": platform.node(),
        "platform": platform.platform(),
        "cpu": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "memory_mb": total_memory_mb(),
        "python": sys.version.split()[0],
        "packages": {name: package_version(name) for name in ("docplex",)},
        "git": git_revision(root),
        "settings": settings,
    }
