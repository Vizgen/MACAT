"""
Lightweight runtime / cost logging for the pipeline (stdlib only, so it imports
in any Python environment).

Records per-stage wall-clock seconds plus the machine context (host, CPU, RAM,
GPU) so we can answer: how long does each pipeline step take, per slide, and does
it need a GPU. Appends one CSV row per stage to study/runtime_log.csv.

Two ways to use it:

  # 1) wrap a whole standalone script's work
  import rtlog
  with rtlog.stage("registration", region=config.REGION, gpu=True):
      main()

  # 2) log a measured duration directly (used by the driver around subprocesses)
  rtlog.log_stage("m0_sections", seconds=12.3, region="<region_key>", section=1)

Logging never raises: any failure inside is swallowed so it can't break a run.
"""
import csv
import os
import platform
import socket
import subprocess
import time

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_log.csv")

_FIELDS = [
    "iso_time", "region", "section", "stage", "seconds",
    "gpu", "host", "cpu", "ram_gb", "gpu_name", "notes",
]

# machine context is constant per process -> compute once
_CTX = None


def _ram_gb():
    """Total RAM in GB from /proc/meminfo (Linux); None elsewhere."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024 / 1024, 1)
    except Exception:
        return None
    return None


def _gpu_name():
    """First GPU name via nvidia-smi (env-independent); '' if none/unavailable."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        names = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        return names[0] if names else ""
    except Exception:
        return ""


def machine_context():
    """Host / CPU / RAM / GPU of the current machine (cached)."""
    global _CTX
    if _CTX is None:
        _CTX = {
            "host": socket.gethostname(),
            "cpu": os.cpu_count(),
            "ram_gb": _ram_gb(),
            "gpu_name": _gpu_name(),
            "platform": platform.platform(),
        }
    return _CTX


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def log_stage(stage, seconds, region="", section="", gpu=False, notes=""):
    """Append one timed-stage row to runtime_log.csv. Never raises."""
    try:
        ctx = machine_context()
        row = {
            "iso_time": _iso_now(),
            "region": region or "",
            "section": section if section != "" else "",
            "stage": stage,
            "seconds": round(float(seconds), 2),
            "gpu": bool(gpu),
            "host": ctx["host"],
            "cpu": ctx["cpu"],
            "ram_gb": ctx["ram_gb"],
            "gpu_name": ctx["gpu_name"],
            "notes": notes or "",
        }
        new = not os.path.exists(LOG_PATH)
        with open(LOG_PATH, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
    except Exception:
        pass  # logging is best-effort; never break the pipeline


class stage:
    """Context manager: time a block and log it on exit (even on exception)."""

    def __init__(self, name, region="", section="", gpu=False, notes=""):
        self.name = name
        self.region = region
        self.section = section
        self.gpu = gpu
        self.notes = notes

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.perf_counter() - self._t0
        note = self.notes
        if exc_type is not None:
            note = (note + " FAILED").strip()
        log_stage(self.name, dt, self.region, self.section, self.gpu, note)
        return False  # do not suppress exceptions
