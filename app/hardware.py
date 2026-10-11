"""Bilgisayarın donanımını tarar: işletim sistemi, CPU, RAM, GPU ve VRAM.

Windows, Linux ve macOS'ta ek bir şey kurmadan çalışır. Bir bilgi
okunamazsa boş bırakılır; tarama asla hata fırlatmaz.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from pathlib import Path

import psutil

GB = 1024**3


def _run(cmd: list[str], timeout: float = 5) -> str:
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            # Windows'ta her çağrıda bir konsol penceresi yanıp sönmesin.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _vendor(name: str) -> str:
    n = name.lower()
    if "nvidia" in n or "geforce" in n or "rtx" in n or "quadro" in n:
        return "NVIDIA"
    if "amd" in n or "radeon" in n:
        return "AMD"
    if "intel" in n:
        return "Intel"
    if "apple" in n:
        return "Apple"
    return ""


# --- GPU --------------------------------------------------------------------------


def _nvidia_smi() -> list[dict]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    out = _run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        name, _, mem = line.rpartition(",")
        try:
            gpus.append({"name": name.strip(), "vendor": "NVIDIA", "vram_gb": round(float(mem) / 1024, 1)})
        except ValueError:
            continue
    return gpus


def _linux_drm() -> list[dict]:
    """amdgpu sürücüsü VRAM'i sysfs'te yayınlıyor."""
    gpus = []
    for card in sorted(Path("/sys/class/drm").glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        vram_file = card / "device" / "mem_info_vram_total"
        if not vram_file.exists():
            continue
        try:
            vram = int(vram_file.read_text().strip()) / GB
        except (OSError, ValueError):
            continue
        name = "AMD GPU"
        try:
            name = (card / "device" / "product_name").read_text().strip() or name
        except OSError:
            pass
        gpus.append({"name": name, "vendor": "AMD", "vram_gb": round(vram, 1)})
    return gpus


def _windows_registry() -> list[dict]:
    """Ekran kartı sürücülerinin kayıt defteri girdileri; 4 GB'ın üstünü de doğru okur."""
    try:
        import winreg
    except ImportError:
        return []
    path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    gpus = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return []
    with root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                    mem = 0
                    for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            v = winreg.QueryValueEx(key, value)[0]
                        except OSError:
                            continue
                        mem = int.from_bytes(v[:8], "little") if isinstance(v, bytes) else int(v)
                        if mem:
                            break
            except OSError:
                continue
            if "basic display" in name.lower() or "remote" in name.lower():
                continue
            gpus.append({"name": name, "vendor": _vendor(name), "vram_gb": round(mem / GB, 1)})
    return gpus


def _gpus(system: str, ram_gb: float) -> list[dict]:
    if system == "Darwin" and platform.machine() == "arm64":
        # Apple Silicon: GPU, RAM'i paylaşıyor; macOS varsayılan olarak ~%70'ini verir.
        return [{"name": "Apple Silicon (birleşik bellek)", "vendor": "Apple", "vram_gb": round(ram_gb * 0.7, 1), "unified": True}]
    gpus = _nvidia_smi()
    if system == "Linux":
        gpus += _linux_drm()
    elif system == "Windows":
        # nvidia-smi NVIDIA kartlarını zaten doğru okudu; kalanları kayıt defterinden al.
        have = {g["name"].lower() for g in gpus}
        gpus += [
            g for g in _windows_registry()
            if g["name"].lower() not in have and not (gpus and g["vendor"] == "NVIDIA")
        ]
    return gpus


# --- CPU ----------------------------------------------------------------------------


def _cpu_name(system: str) -> str:
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif system == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except (ImportError, OSError):
            pass
    elif system == "Darwin":
        name = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if name:
            return name
    return platform.processor() or platform.machine() or "bilinmiyor"


# --- genel -----------------------------------------------------------------------------


def scan() -> dict:
    system = platform.system()
    vm = psutil.virtual_memory()
    ram_gb = round(vm.total / GB, 1)
    gpus = _gpus(system, ram_gb)

    # Ollama'nın kullanabildiği GPU'lar: NVIDIA (CUDA), AMD (ROCm), Apple (Metal).
    # Intel'in paylaşımlı belleği varsayılan kurulumda kullanılmıyor.
    usable = [g for g in gpus if g["vendor"] in {"NVIDIA", "AMD", "Apple"} and g["vram_gb"] >= 2]
    try:
        disk_free = round(psutil.disk_usage(str(Path.home())).free / GB, 1)
    except OSError:
        disk_free = None

    return {
        "os": f"{system} {platform.release()}".strip(),
        "cpu": _cpu_name(system),
        "cores": psutil.cpu_count(logical=False) or 0,
        "threads": psutil.cpu_count(logical=True) or 0,
        "ram_gb": ram_gb,
        "ram_free_gb": round(vm.available / GB, 1),
        "gpus": gpus,
        "vram_gb": round(sum(g["vram_gb"] for g in usable), 1),
        "unified": any(g.get("unified") for g in usable),
        "disk_free_gb": disk_free,
    }
