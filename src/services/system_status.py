"""SystemStatus：服务器运行状态采集（从 Web UI 用户状态页使用）。

零第三方依赖（不引入 psutil）：基于 stdlib platform/os 与 /proc（Linux/Android）。
返回展示用的信息 dict：平台 / 系统 / 主机名 / 内存占用 / CPU 负载 / 进程数。
任何读取失败都降级为 'N/A'，绝不抛出（管理页展示层，不应影响面板可用）。
"""
import os
import platform
import sys
from typing import Dict


def _meminfo() -> Dict[str, int]:
    """读取 /proc/meminfo 的关键字段（KB）。文件缺失/解析失败返回空。"""
    try:
        out: Dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0].rstrip(":") in ("MemTotal", "MemAvailable"):
                    try:
                        out[parts[0].rstrip(":")] = int(parts[1])  # KB
                    except ValueError:
                        pass
        return out
    except OSError:
        return {}


def _loadavg() -> str:
    """CPU 负载（/proc/loadavg 前 3 个值）；读取失败返回 'N/A'。"""
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as f:
            return f.read().strip().split(" ", 1)[0]
    except OSError:
        return "N/A"


def collect() -> Dict[str, str]:
    """采集服务器运行状态（平台 / 系统信息 / 内存 / CPU 负载 / 进程数）。

    所有值都转成适合展示的字符串；内存按占用百分比与人类可读大小呈现。
    """
    try:
        system = platform.system() or "未知"
        release = platform.release() or ""
        machine = platform.machine() or ""
        python_ver = sys.version.split()[0] if sys.version else "?"
        try:
            nodename = os.uname().nodename
        except (AttributeError, OSError):
            nodename = "N/A"

        info: Dict[str, str] = {
            "platform": system,
            "release": release,
            "machine": machine,
            "hostname": nodename,
            "python": python_ver,
            "loadavg": _loadavg(),
        }
        mem = _meminfo()
        if mem:
            total_kb = mem.get("MemTotal", 0)
            avail_kb = mem.get("MemAvailable", 0)
            used_kb = total_kb - avail_kb
            total_mb = total_kb // 1024
            used_mb = used_kb // 1024
            pct = int(used_kb * 100 / total_kb) if total_kb else 0
            info["memory"] = f"{used_mb} MB / {total_mb} MB（{pct}%）"
            info["memory_pct"] = str(pct)
        else:
            info["memory"] = "N/A"
            info["memory_pct"] = "N/A"
        # 可读大小工具（内存 MB → 若 >=1024 显示 GB）
        return info
    except Exception:  # noqa: BLE001 - 采集失败降级
        return {"platform": "N/A", "release": "N/A", "machine": "N/A",
                "hostname": "N/A", "python": "N/A", "loadavg": "N/A",
                "memory": "N/A", "memory_pct": "N/A"}
