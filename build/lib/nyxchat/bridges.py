"""Auto-fetch obfs4 + webtunnel bridges from multiple resilient sources.
Optimized for heavy censorship environments (Iran, Russia, China)."""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

# Fallback hard-coded bridges (real ones that have historically worked).
# These are last-resort only; live fetch is preferred.
DEFAULT_OBFS4 = [
    "Bridge obfs4 192.95.36.142:443 CDF2E852BF539B82BD10E27E9115A31734E38C4E cert=qUVQ0srLMeLEQX7NPnjTnBIlR51WLw1aK3IVzHnq4dYfVF3kGv/5t+K8p6h8qYq9 iat-mode=0",
]

DEFAULT_WEBTUNNEL = []  # WebTunnel needs fresh URLs; empty is safer than stale

# Snowflake bridge lines (standard built-in ones used by Tor Browser).
# These do not need real IPs; the broker finds proxies.
SNOWFLAKE_BRIDGES = [
    # Current Tor Browser defaults (CDN77 + datapacket fronts)
    (
        "Bridge snowflake 192.0.2.3:80 2B280B23E1107BB62ABFC40DDCC8824814F80A72 "
        "fingerprint=2B280B23E1107BB62ABFC40DDCC8824814F80A72 "
        "url=https://1098762253.rsc.cdn77.org/ "
        "fronts=app.datapacket.com,www.datapacket.com "
        "ice=stun:stun.epygi.com:3478,stun:stun.uls.co.za:3478,"
        "stun:stun.voipgate.com:3478,stun:stun.mixvoip.com:3478,"
        "stun:stun.nextcloud.com:3478,stun:stun.bethesda.net:3478,"
        "stun:stun.nextcloud.com:443 "
        "utls-imitate=hellorandomizedalpn"
    ),
    (
        "Bridge snowflake 192.0.2.4:80 8838024498816A039FCBBAB14E6F40A0843051FA "
        "fingerprint=8838024498816A039FCBBAB14E6F40A0843051FA "
        "url=https://1098762253.rsc.cdn77.org/ "
        "fronts=app.datapacket.com,www.datapacket.com "
        "ice=stun:stun.epygi.com:3478,stun:stun.uls.co.za:3478,"
        "stun:stun.voipgate.com:3478,stun:stun.mixvoip.com:3478,"
        "stun:stun.nextcloud.com:3478,stun:stun.bethesda.net:3478,"
        "stun:stun.nextcloud.com:443 "
        "utls-imitate=hellorandomizedalpn"
    ),
    # AMP-cache fallback (when domain fronting is restricted)
    (
        "Bridge snowflake 192.0.2.5:80 2B280B23E1107BB62ABFC40DDCC8824814F80A72 "
        "fingerprint=2B280B23E1107BB62ABFC40DDCC8824814F80A72 "
        "url=https://snowflake-broker.torproject.net/ "
        "ampcache=https://cdn.ampproject.org/ "
        "front=www.google.com "
        "ice=stun:stun.l.google.com:19302,stun:stun.antisip.com:3478,"
        "stun:stun.epygi.com:3478,stun:stun.uls.co.za:3478,"
        "stun:stun.voipgate.com:3478 "
        "utls-imitate=hellorandomizedalpn"
    ),
]


def _fetch(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "nyx/0.2"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_from_bridgedb(transport: str = "obfs4") -> list[str]:
    """Official BridgeDB (may be blocked in Iran, but try anyway)."""
    bridges = []
    try:
        data = _fetch(f"https://bridges.torproject.org/bridges?transport={transport}")
        for line in data.splitlines():
            line = line.strip()
            if line.startswith(f"Bridge {transport} ") or line.startswith(f"{transport} "):
                if not line.startswith("Bridge "):
                    line = "Bridge " + line
                bridges.append(line)
    except Exception:
        pass
    return bridges


def fetch_from_github_collector(transport: str) -> list[str]:
    """Hourly-tested bridges from OnionHop collector (very useful)."""
    bridges = []
    # Prefer the "tested" list, fall back to 72h / full
    urls = [
        f"https://raw.githubusercontent.com/center2055/OnionHop-Bridges-Collector/main/bridge/{transport}_tested.txt",
        f"https://raw.githubusercontent.com/center2055/OnionHop-Bridges-Collector/main/bridge/{transport}_72h.txt",
        f"https://raw.githubusercontent.com/center2055/OnionHop-Bridges-Collector/main/bridge/{transport}.txt",
    ]
    for url in urls:
        try:
            data = _fetch(url, timeout=10)
            for line in data.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Normalize
                if line.startswith("Bridge "):
                    bridges.append(line)
                elif line.startswith(f"{transport} "):
                    bridges.append("Bridge " + line)
                else:
                    # some lists omit the transport name
                    bridges.append(f"Bridge {transport} {line}")
            if bridges:
                break
        except Exception:
            continue
    return bridges


def fetch_from_torproject_github(transport: str = "obfs4") -> list[str]:
    """Official-ish community lists."""
    bridges = []
    urls = [
        f"https://raw.githubusercontent.com/torproject/bridges/main/{transport}.txt",
        "https://raw.githubusercontent.com/torproject/bridges/main/obfs4.txt",
    ]
    for url in urls:
        try:
            data = _fetch(url, timeout=8)
            for line in data.splitlines():
                line = line.strip()
                if line.startswith("Bridge ") or line.startswith(f"{transport} "):
                    if not line.startswith("Bridge "):
                        line = "Bridge " + line
                    bridges.append(line)
            if bridges:
                break
        except Exception:
            continue
    return bridges


def get_bridges(transport: str = "obfs4", max_count: int = 30) -> list[str]:
    """Return a de-duplicated list of bridges for the given transport."""
    all_bridges: list[str] = []
    seen: set[str] = set()

    sources = [
        lambda: fetch_from_github_collector(transport),
        lambda: fetch_from_bridgedb(transport),
        lambda: fetch_from_torproject_github(transport),
    ]

    for src in sources:
        try:
            for b in src():
                # normalize key for dedup (IP:port + fingerprint if present)
                key = b.strip()
                if key not in seen:
                    seen.add(key)
                    all_bridges.append(b)
        except Exception:
            continue

    if not all_bridges:
        if transport == "obfs4":
            all_bridges = DEFAULT_OBFS4[:]
        elif transport == "webtunnel":
            all_bridges = DEFAULT_WEBTUNNEL[:]

    return all_bridges[:max_count]


def get_snowflake_bridges() -> list[str]:
    return SNOWFLAKE_BRIDGES[:]


if __name__ == "__main__":
    print("=== Snowflake ===")
    for b in get_snowflake_bridges()[:2]:
        print(" ", b[:100] + "...")

    print("\n=== WebTunnel (live) ===")
    wt = get_bridges("webtunnel", max_count=5)
    print(f"Found {len(wt)}")
    for b in wt[:3]:
        print(" ", b[:110] + "...")

    print("\n=== obfs4 (live) ===")
    ob = get_bridges("obfs4", max_count=5)
    print(f"Found {len(ob)}")
    for b in ob[:3]:
        print(" ", b[:110] + "...")
