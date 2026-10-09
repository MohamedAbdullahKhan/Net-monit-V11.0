# =============================================================================
# Net-monit V11.0
# Copyright (c) 2024-2026 Abdullah. All rights reserved.
# Contact: abuabdullah.be@outlook.com
# Unauthorised copying, modification or distribution of this software
# is strictly prohibited without prior written permission from the author.
# =============================================================================
"""
ping_check.py
Cross-platform ICMP ping using the OS's own `ping` binary via subprocess.
This avoids needing raw-socket / root privileges that libraries like
ping3 require on Linux.

Returns: {"reachable": bool, "latency_ms": float|None, "packet_loss_pct": float}
"""
import platform
import re
import subprocess


def check(host, timeout_ms=1000, count=4):
    system = platform.system().lower()

    if system == "windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms), host]
    else:
        # -W is seconds on Linux ping; round up
        timeout_s = max(1, int(round(timeout_ms / 1000)))
        cmd = ["ping", "-c", str(count), "-W", str(timeout_s), host]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=(count * timeout_ms / 1000) + 5
        )
        output = result.stdout
    except subprocess.TimeoutExpired:
        return {"reachable": False, "latency_ms": None, "packet_loss_pct": 100.0, "error": "ping timed out"}
    except FileNotFoundError:
        return {"reachable": False, "latency_ms": None, "packet_loss_pct": 100.0, "error": "ping binary not found"}

    loss_pct = _parse_packet_loss(output, system)
    avg_latency = _parse_avg_latency(output, system)

    reachable = loss_pct is not None and loss_pct < 100.0
    return {
        "reachable": reachable,
        "latency_ms": avg_latency,
        "packet_loss_pct": loss_pct if loss_pct is not None else 100.0,
    }


def _parse_packet_loss(output, system):
    match = re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:packet )?loss", output, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _parse_avg_latency(output, system):
    if system == "windows":
        match = re.search(r"Average\s*=\s*(\d+)\s*ms", output, re.IGNORECASE)
        if match:
            return float(match.group(1))
    else:
        # Linux/Mac: rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms
        match = re.search(r"=\s*[\d.]+/([\d.]+)/[\d.]+", output)
        if match:
            return float(match.group(1))
    return None
