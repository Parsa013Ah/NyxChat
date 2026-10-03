"""Auto-install Python deps, private Tor binary, and pluggable transports.
Robust on Windows. Prefers extracting lyrebird from Tor Expert Bundle.
"""
import os, sys, subprocess, shutil, platform, tarfile, tempfile, json
import urllib.request, zipfile, io, time, re
from pathlib import Path

HOME = Path(os.environ.get("NYX_HOME") or os.path.expanduser("~/.nyx"))
HOME.mkdir(mode=0o700, exist_ok=True)
BIN = HOME / "bin"
BIN.mkdir(parents=True, exist_ok=True)

IS_WIN = platform.system() == "Windows"
TOR_EXE = "tor.exe" if IS_WIN else "tor"
LYREBIRD_EXE = "lyrebird.exe" if IS_WIN else "lyrebird"
SNOWFLAKE_EXE = "snowflake-client.exe" if IS_WIN else "snowflake-client"

_ensured = False


def log(m):
    print(f"[nyx] {m}", file=sys.stderr, flush=True)


def _need(mod):
    try:
        __import__(mod)
        return False
    except ImportError:
        return True


def _pip(args):
    for extra in (["--user"], []):
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install",
                 "--quiet", "--disable-pip-version-check", *extra, *args],
                stdout=subprocess.DEVNULL,
            )
            return True
        except subprocess.CalledProcessError:
            continue
    return False


def ensure_python_deps():
    missing = []
    for mod, pkg in (("websocket", "websocket-client"),
                     ("socks", "PySocks"),
                     ("requests", "requests"),
                     ("cryptography", "cryptography")):
        if _need(mod):
            missing.append(pkg)
    if IS_WIN and _need("curses"):
        missing.append("windows-curses")
    if not missing:
        return
    log(f"installing python deps: {', '.join(missing)}")
    if not _pip(missing):
        log("pip install failed — install manually:")
        log("  " + sys.executable + " -m pip install " + " ".join(missing))
        sys.exit(1)


def _tor_tag():
    s, m = platform.system(), platform.machine().lower()
    if s == "Linux":     o = "linux"
    elif s == "Darwin":  o = "macos"
    elif s == "Windows": o = "windows"
    else: return None
    if m in ("x86_64", "amd64"): a = "x86_64"
    elif m in ("aarch64", "arm64"): a = "aarch64"
    else: return None
    return o, a


def _find_existing_tor():
    if shutil.which(TOR_EXE):
        return Path(shutil.which(TOR_EXE)).parent
    home = Path.home()
    if IS_WIN:
        for d in (
            home / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor",
            home / "Downloads" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor",
            home / "Tor Browser" / "Browser" / "TorBrowser" / "Tor",
            Path("C:/Program Files/Tor Browser/Browser/TorBrowser/Tor"),
            Path("C:/Program Files (x86)/Tor Browser/Browser/TorBrowser/Tor"),
        ):
            if (d / TOR_EXE).exists():
                return d
    elif platform.system() == "Darwin":
        for d in (
            Path("/Applications/Tor Browser.app/Contents/MacOS/Tor"),
            Path("/opt/homebrew/bin"),
            Path("/usr/local/bin"),
        ):
            if (d / TOR_EXE).exists():
                return d
    else:
        for d in (Path("/usr/bin"), Path("/usr/local/bin"), Path("/opt/bin")):
            if (d / TOR_EXE).exists():
                return d
    return None


def _download(url, dest, label="file"):
    """Download with progress. Returns True on success."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nyx/0.2"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = last = 0
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct != last:
                        print(f"\r[nyx] {label} {pct}%", end="", file=sys.stderr, flush=True)
                        last = pct
        print("", file=sys.stderr)
        return True
    except Exception as e:
        log(f"download failed ({label}): {e}")
        return False


def _fetch_tor(url, o, a):
    tmp = BIN / ".tor-dl"
    try:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        tgz = tmp / "tor.tar.gz"

        free = shutil.disk_usage(str(BIN)).free
        if free < 200 * 1024 * 1024:
            log(f"not enough disk space ({free // (1024*1024)} MB free, need ~200 MB)")
            return False

        if not _download(url, tgz, "Tor"):
            return False

        with tarfile.open(tgz, "r:gz") as t:
            try:
                t.extractall(tmp / "x", filter="data")
            except TypeError:
                t.extractall(tmp / "x")

        target = "tor.exe" if o == "windows" else "tor"
        pt_names = {
            "lyrebird", "lyrebird.exe",
            "obfs4proxy", "obfs4proxy.exe",
            "snowflake-client", "snowflake-client.exe",
            "conjure-client", "conjure-client.exe",
        }
        found_tor = False
        # Walk the ENTIRE extracted tree (PTs live in pluggable_transports/)
        for root, _dirs, files in os.walk(tmp / "x"):
            rootp = Path(root)
            for f in files:
                src_file = rootp / f
                if f == target:
                    try:
                        shutil.copy2(src_file, BIN / f)
                        (BIN / f).chmod(0o755)
                        found_tor = True
                        log(f"tor installed: {BIN / f}")
                    except Exception as e:
                        log(f"copy tor failed: {e}")
                elif f in pt_names:
                    try:
                        shutil.copy2(src_file, BIN / f)
                        (BIN / f).chmod(0o755)
                        log(f"extracted PT: {f}")
                    except Exception as e:
                        log(f"copy PT {f} failed: {e}")
                # also copy companion DLLs next to tor on Windows
                elif IS_WIN and f.lower().endswith(".dll"):
                    try:
                        shutil.copy2(src_file, BIN / f)
                    except Exception:
                        pass
        if found_tor:
            return True
        log("tor not found inside archive")
        return False
    except Exception as e:
        log(f"download failed: {e}")
        return False
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def ensure_tor():
    # If tor exists but PT is missing, still try to re-fetch expert bundle once
    if (BIN / TOR_EXE).exists() and _pt_binary():
        return True
    existing = _find_existing_tor()
    if existing:
        log(f"using existing tor at {existing}")
        for f in existing.iterdir():
            if f.is_file():
                try:
                    shutil.copy2(f, BIN / f.name)
                except Exception:
                    pass
        return (BIN / TOR_EXE).exists()

    tag = _tor_tag()
    if not tag:
        log("unsupported platform for auto Tor install")
        return False
    o, a = tag

    version = "15.0.24"
    try:
        req = urllib.request.Request(
            "https://aus1.torproject.org/torbrowser/update_3/release/downloads.json",
            headers={"User-Agent": "nyx/0.2"},
        )
        meta = json.loads(urllib.request.urlopen(req, timeout=10).read())
        version = meta.get("version") or version
    except Exception:
        pass

    fname = f"tor-expert-bundle-{o}-{a}-{version}.tar.gz"
    urls = [
        f"https://archive.torproject.org/tor-package-archive/torbrowser/{version}/{fname}",
        f"https://dist.torproject.org/torbrowser/{version}/{fname}",
        f"https://www.torproject.org/dist/torbrowser/{version}/{fname}",
    ]
    log(f"downloading Tor {version} for {o}/{a}…")
    for url in urls:
        if _fetch_tor(url, o, a):
            return True
    log("all Tor mirrors failed")
    log("install manually: https://www.torproject.org/download/tor/")
    return False


def _latest_go_version():
    """Fetch the latest stable Go version string, e.g. '1.27.1'."""
    try:
        req = urllib.request.Request(
            "https://go.dev/dl/?mode=json",
            headers={"User-Agent": "nyx/0.2"},
        )
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        for rel in data:
            if rel.get("stable"):
                ver = rel["version"]  # e.g. "go1.27.1"
                return ver[2:] if ver.startswith("go") else ver
    except Exception as e:
        log(f"could not query latest Go version: {e}")
    return "1.27.1"  # safe recent fallback


def _ensure_go():
    if shutil.which("go"):
        return True
    if not IS_WIN:
        return False

    # Try winget first
    log("trying winget for Go…")
    try:
        subprocess.check_call([
            "winget", "install", "--id", "GoLang.Go",
            "--accept-source-agreements", "--accept-package-agreements",
            "--silent",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        # refresh PATH
        os.environ["PATH"] = (
            str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Go" / "bin")
            + os.pathsep + os.environ.get("PATH", "")
        )
        if shutil.which("go"):
            return True
    except Exception:
        pass

    # Manual download of latest stable
    go_ver = _latest_go_version()
    arch = "amd64" if platform.machine().lower() in ("x86_64", "amd64", "AMD64") else "arm64"
    url = f"https://go.dev/dl/go{go_ver}.windows-{arch}.zip"
    log(f"downloading Go {go_ver}…")
    tmp = BIN / ".go-dl"
    try:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        zpath = tmp / "go.zip"
        if not _download(url, zpath, "Go"):
            return False
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp / "x")
        go_dir = tmp / "x" / "go"
        dest = BIN / "go"
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(go_dir, dest)
        os.environ["PATH"] = str(dest / "bin") + os.pathsep + os.environ.get("PATH", "")
        if shutil.which("go") or (dest / "bin" / "go.exe").exists():
            # make sure go is on PATH for this process
            os.environ["PATH"] = str(dest / "bin") + os.pathsep + os.environ.get("PATH", "")
            log(f"Go installed: {dest}")
            return True
    except Exception as e:
        log(f"Go install failed: {e}")
    return False


def _build_lyrebird_from_source():
    target = BIN / LYREBIRD_EXE
    if not _ensure_go():
        return False
    log("building lyrebird from source…")
    try:
        tmp = BIN / ".lyrebird-build"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        clone_urls = [
            "https://gitlab.torproject.org/tpo/anti-censorship/pluggable-transports/lyrebird.git",
            "https://github.com/torproject/lyrebird.git",
        ]
        cloned = False
        for url in clone_urls:
            try:
                subprocess.check_call(
                    ["git", "clone", "--depth", "1", url, str(tmp / "src")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=90,
                )
                cloned = True
                break
            except Exception:
                continue
        if not cloned:
            log("could not clone lyrebird source (git required)")
            return False

        env = os.environ.copy()
        env["GOPATH"] = str(tmp / "gopath")
        env["GOCACHE"] = str(tmp / "gocache")
        env["CGO_ENABLED"] = "0"
        # find go binary
        go_bin = shutil.which("go") or str(BIN / "go" / "bin" / ("go.exe" if IS_WIN else "go"))
        subprocess.check_call(
            [go_bin, "build", "-o", str(target), "./cmd/lyrebird"],
            cwd=str(tmp / "src"), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=300,
        )
        if target.exists():
            try:
                target.chmod(0o755)
            except OSError:
                pass
            shutil.rmtree(tmp, ignore_errors=True)
            log(f"lyrebird built: {target}")
            return True
    except Exception as e:
        log(f"lyrebird build failed: {e}")
    return False


def _extract_pt_from_tor_browser():
    """Copy lyrebird / snowflake-client / obfs4proxy from an existing Tor Browser install."""
    home = Path.home()
    search = []
    if IS_WIN:
        bases = [
            home / "Desktop" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor",
            home / "Downloads" / "Tor Browser" / "Browser" / "TorBrowser" / "Tor",
            home / "Tor Browser" / "Browser" / "TorBrowser" / "Tor",
            Path("C:/Program Files/Tor Browser/Browser/TorBrowser/Tor"),
            Path("C:/Program Files (x86)/Tor Browser/Browser/TorBrowser/Tor"),
        ]
        for b in bases:
            search.append(b)
            search.append(b / "PluggableTransports")
            search.append(b / "pluggable_transports")
    elif platform.system() == "Darwin":
        b = Path("/Applications/Tor Browser.app/Contents/MacOS/Tor")
        search = [b, b / "PluggableTransports", b / "pluggable_transports"]
    else:
        bases = [
            home / "tor-browser" / "Browser" / "TorBrowser" / "Tor",
            home / "tor-browser_en-US" / "Browser" / "TorBrowser" / "Tor",
            Path("/opt/tor-browser/Browser/TorBrowser/Tor"),
        ]
        for b in bases:
            search.append(b)
            search.append(b / "PluggableTransports")
            search.append(b / "pluggable_transports")
    names = ("lyrebird.exe", "lyrebird", "snowflake-client.exe", "snowflake-client",
             "obfs4proxy.exe", "obfs4proxy")
    found_any = False
    for d in search:
        if not d.exists():
            continue
        for name in names:
            p = d / name
            if p.exists():
                dest = BIN / name
                try:
                    shutil.copy2(p, dest)
                    try:
                        dest.chmod(0o755)
                    except OSError:
                        pass
                    log(f"copied {name} from Tor Browser")
                    found_any = True
                except Exception as e:
                    log(f"copy failed {name}: {e}")
    return found_any


def _pt_binary():
    """Return path to the best available pluggable-transport binary, or None."""
    for name in (LYREBIRD_EXE, "lyrebird", "lyrebird.exe",
                 SNOWFLAKE_EXE, "snowflake-client", "snowflake-client.exe",
                 "obfs4proxy", "obfs4proxy.exe"):
        p = BIN / name
        if p.exists():
            return p
        # also check PATH
        w = shutil.which(name)
        if w:
            return Path(w)
    return None


def ensure_pt():
    """Ensure at least one pluggable transport binary is available."""
    if _pt_binary():
        return True

    # 1. Try Tor Browser install
    if _extract_pt_from_tor_browser():
        if _pt_binary():
            return True

    # 2. Expert bundle may already have put lyrebird into BIN during ensure_tor
    if _pt_binary():
        return True

    # 3. Build from source (needs Go + git)
    if _build_lyrebird_from_source():
        return True

    log("WARNING: no pluggable transport binary found")
    log("  → Snowflake / WebTunnel / obfs4 will not work")
    log("  → Install Tor Browser or put lyrebird.exe into ~/.nyx/bin/")
    return False


def write_torrc(mode: str = "snowflake") -> Path | None:
    """Write a torrc for the requested transport.
    Returns path or None if PT binary is missing for that mode.
    """
    from nyxchat.bridges import get_bridges, get_snowflake_bridges

    pt = _pt_binary()
    if pt is None and mode != "direct":
        log(f"cannot write {mode} torrc — no PT binary")
        return None

    tor_dir = HOME / "tor"
    tor_dir.mkdir(parents=True, exist_ok=True)
    torrc = tor_dir / "torrc"
    pt_path = str(pt) if pt else ""

    lines = ["ClientOnly 1"]

    if mode == "snowflake":
        lines.append("UseBridges 1")
        # lyrebird implements snowflake; legacy snowflake-client also works
        lines.append(f"ClientTransportPlugin snowflake exec {pt_path}")
        lines.extend(get_snowflake_bridges())
        log("torrc: Snowflake mode")
    elif mode == "webtunnel":
        bridges = get_bridges("webtunnel", max_count=20)
        if not bridges:
            log("no webtunnel bridges — falling back to obfs4")
            return write_torrc("obfs4")
        lines.append("UseBridges 1")
        lines.append(f"ClientTransportPlugin webtunnel exec {pt_path}")
        lines.extend(bridges)
        log(f"torrc: WebTunnel mode ({len(bridges)} bridges)")
    elif mode == "obfs4":
        bridges = get_bridges("obfs4", max_count=25)
        lines.append("UseBridges 1")
        lines.append(f"ClientTransportPlugin obfs4 exec {pt_path}")
        lines.extend(bridges)
        log(f"torrc: obfs4 mode ({len(bridges)} bridges)")
    else:  # direct — no bridges
        log("torrc: direct mode (no bridges)")

    content = "\n".join(lines) + "\n"
    torrc.write_text(content, encoding="utf-8")
    try:
        os.chmod(torrc, 0o600)
    except OSError:
        pass
    log(f"torrc written: {torrc}")
    return torrc


def ensure_all(preferred_mode: str = "webtunnel"):
    """Install everything and write an initial torrc."""
    global _ensured
    if _ensured:
        return
    ensure_python_deps()
    ensure_tor()
    has_pt = ensure_pt()
    if has_pt:
        write_torrc(preferred_mode)
    else:
        # No PT — write a minimal direct config so Tor at least starts
        write_torrc("direct")
        log("running without bridges (PT binary missing)")
    os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "")
    _ensured = True


# Compatibility aliases
def write_snowflake_torrc():
    return write_torrc("snowflake")


def write_obfs4_torrc():
    return write_torrc("obfs4")


def _remove_torrc():
    torrc = HOME / "tor" / "torrc"
    if torrc.exists():
        try:
            torrc.unlink()
        except OSError:
            pass
