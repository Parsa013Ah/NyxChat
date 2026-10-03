"""Nostr core: pure-python secp256k1, NIP-44 v2, NIP-59/17, Blossom files, Tor.
All network I/O is forced through Tor. No clearnet fallback."""
from __future__ import annotations

import base64, hashlib, hmac, io, json, os, queue, re, secrets, shutil, sys
import socket, subprocess, threading, time
from collections import OrderedDict
from pathlib import Path

import requests
import websocket
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ══════════════════ secp256k1 ══════════════════
P = 2 ** 256 - 2 ** 32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8, 1)
INF = (0, 1, 0)


def _dbl(p):
    X, Y, Z = p
    if Z == 0 or Y == 0:
        return INF
    S = 4 * X * Y * Y % P
    M = 3 * X * X % P
    X3 = (M * M - 2 * S) % P
    Y3 = (M * (S - X3) - 8 * pow(Y, 4, P)) % P
    return X3, Y3, 2 * Y * Z % P


def _add(p, q):
    X1, Y1, Z1 = p; X2, Y2, Z2 = q
    if Z1 == 0: return q
    if Z2 == 0: return p
    a, b = Z1 * Z1 % P, Z2 * Z2 % P
    U1, U2 = X1 * b % P, X2 * a % P
    S1, S2 = Y1 * b * Z2 % P, Y2 * a * Z1 % P
    if U1 == U2:
        return _dbl(p) if S1 == S2 else INF
    H, R = U2 - U1, S2 - S1
    H2 = H * H % P; H3 = H * H2 % P; V = U1 * H2 % P
    X3 = (R * R - H3 - 2 * V) % P
    return X3, (R * (V - X3) - S1 * H3) % P, H * Z1 * Z2 % P


def _mul(k, pt):
    r = INF
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _dbl(pt)
        k >>= 1
    return r


def _aff(p):
    if p[2] == 0:
        return None
    zi = pow(p[2], -1, P)
    z2 = zi * zi % P
    return p[0] * z2 % P, p[1] * z2 * zi % P


def lift_x(x):
    if x >= P:
        return None
    y2 = (pow(x, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    if y * y % P != y2:
        return None
    return x, (y if y % 2 == 0 else P - y), 1


def _th(tag, *parts):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + b"".join(parts)).digest()


def new_sk():
    return secrets.randbelow(N - 1) + 1


def pubhex(sk):
    return _aff(_mul(sk, G))[0].to_bytes(32, "big").hex()


def sign(msg, sk):
    pt = _aff(_mul(sk, G))
    d = sk if pt[1] % 2 == 0 else N - sk
    px = pt[0].to_bytes(32, "big")
    t = (d ^ int.from_bytes(_th("BIP0340/aux", os.urandom(32)), "big")).to_bytes(32, "big")
    k = int.from_bytes(_th("BIP0340/nonce", t, px, msg), "big") % N
    R = _aff(_mul(k, G))
    if R[1] % 2:
        k = N - k
    rb = R[0].to_bytes(32, "big")
    e = int.from_bytes(_th("BIP0340/challenge", rb, px, msg), "big") % N
    return rb + ((k + e * d) % N).to_bytes(32, "big")


def verify(msg, pub_hex, sig):
    try:
        if len(pub_hex) != 64 or len(sig) != 64:
            return False
        Pt = lift_x(int(pub_hex, 16))
        r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
        if Pt is None or r >= P or s >= N:
            return False
        e = int.from_bytes(_th("BIP0340/challenge", sig[:32],
                               bytes.fromhex(pub_hex), msg), "big") % N
        R = _aff(_add(_mul(s, G), _mul(N - e, Pt)))
        return R is not None and R[1] % 2 == 0 and R[0] == r
    except Exception:
        return False


# ══════════════════ bech32 / NIP-19 ══════════════════
_CH = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _cb(data, f, t, pad):
    acc = bits = 0
    out = []
    for v in data:
        acc = (acc << f) | v
        bits += f
        while bits >= t:
            bits -= t
            out.append((acc >> bits) & ((1 << t) - 1))
    if pad and bits:
        out.append((acc << (t - bits)) & ((1 << t) - 1))
    return out


def _poly(v):
    g = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    c = 1
    for x in v:
        b = c >> 25
        c = ((c & 0x1ffffff) << 5) ^ x
        for i in range(5):
            if (b >> i) & 1:
                c ^= g[i]
    return c


def _hrp(h):
    return [ord(x) >> 5 for x in h] + [0] + [ord(x) & 31 for x in h]


def bech_enc(hrp, data):
    d = _cb(data, 8, 5, True)
    chk = _poly(_hrp(hrp) + d + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CH[x] for x in d + [(chk >> 5 * (5 - i)) & 31 for i in range(6)])


def bech_dec(s):
    hrp, _, rest = s.strip().lower().rpartition("1")
    d = [_CH.index(c) for c in rest]
    if _poly(_hrp(hrp) + d) != 1:
        raise ValueError("bad checksum")
    return hrp, bytes(_cb(d[:-6], 5, 8, False))


def npub(pk_hex):
    return bech_enc("npub", bytes.fromhex(pk_hex))


def nsec(sk):
    return bech_enc("nsec", sk.to_bytes(32, "big"))


def parse_pub(s):
    s = s.strip()
    if s.startswith("npub1"):
        data = bech_dec(s)[1]
        if len(data) != 32:
            raise ValueError("bad npub length")
        return data.hex()
    if s.startswith("nprofile1"):
        data = bech_dec(s)[1]
        if len(data) < 32:
            raise ValueError("bad nprofile")
        return data[:32].hex()
    if re.fullmatch(r"[0-9a-fA-F]{64}", s):
        return s.lower()
    raise ValueError("not an npub / nprofile / hex key")


def short(pk_hex):
    n = npub(pk_hex)
    return n[:9] + "…" + n[-4:]


# ══════════════════ NIP-44 v2 ══════════════════
_CACHE_MAX = 1024
_conv_cache: "OrderedDict[tuple, bytes]" = OrderedDict()


def conv_key(sk, pub_hex):
    k = (sk, pub_hex)
    c = _conv_cache.get(k)
    if c is not None:
        _conv_cache.move_to_end(k)
        return c
    Pt = lift_x(int(pub_hex, 16))
    if Pt is None:
        raise ValueError("bad pubkey")
    shared = _aff(_mul(sk, Pt))[0].to_bytes(32, "big")
    ck = hmac.new(b"nip44-v2", shared, hashlib.sha256).digest()
    _conv_cache[k] = ck
    if len(_conv_cache) > _CACHE_MAX:
        _conv_cache.popitem(last=False)
    return ck


def _padlen(n):
    if n <= 32:
        return 32
    p2 = 1 << (n - 1).bit_length()
    ch = 32 if p2 <= 256 else p2 // 8
    return ch * ((n - 1) // ch + 1)


def _keys(ck, nonce):
    t1 = hmac.new(ck, nonce + b"\x01", hashlib.sha256).digest()
    t2 = hmac.new(ck, t1 + nonce + b"\x02", hashlib.sha256).digest()
    t3 = hmac.new(ck, t2 + nonce + b"\x03", hashlib.sha256).digest()
    o = (t1 + t2 + t3)[:76]
    return o[:32], o[32:44], o[44:]


def _chacha(key, n12, data):
    return Cipher(algorithms.ChaCha20(key, b"\0\0\0\0" + n12),
                  mode=None).encryptor().update(data)


def nip44_encrypt(text, ck):
    b = text.encode()
    if not 1 <= len(b) <= 65535:
        raise ValueError("message length out of range")
    nonce = os.urandom(32)
    ek, n12, hk = _keys(ck, nonce)
    pl = _padlen(len(b))
    ct = _chacha(ek, n12, len(b).to_bytes(2, "big") + b + b"\0" * (pl - len(b)))
    mac = hmac.new(hk, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(b"\x02" + nonce + ct + mac).decode()


def nip44_decrypt(payload, ck):
    raw = base64.b64decode(payload)
    if len(raw) < 99 or raw[0] != 2:
        raise ValueError("bad payload header")
    nonce, ct, mac = raw[1:33], raw[33:-32], raw[-32:]
    ek, n12, hk = _keys(ck, nonce)
    if not hmac.compare_digest(mac, hmac.new(hk, nonce + ct, hashlib.sha256).digest()):
        raise ValueError("bad mac")
    pl = _chacha(ek, n12, ct)
    n = int.from_bytes(pl[:2], "big")
    if n == 0 or len(pl) != 2 + _padlen(n):
        raise ValueError("bad padding")
    return pl[2:2 + n].decode()


# ══════════════════ events / NIP-59 ══════════════════
def _j(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


def ev_id(pub, ts, kind, tags, content):
    return hashlib.sha256(_j([0, pub, ts, kind, tags, content]).encode()).hexdigest()


def make_event(sk, kind, content, tags=None, ts=None):
    pub = pubhex(sk)
    tags = tags or []
    if ts is None:
        ts = int(time.time())
    eid = ev_id(pub, ts, kind, tags, content)
    return dict(id=eid, pubkey=pub, created_at=ts, kind=kind,
                tags=tags, content=content,
                sig=sign(bytes.fromhex(eid), sk).hex())


def verify_event(ev):
    try:
        if ev["id"] != ev_id(ev["pubkey"], ev["created_at"], ev["kind"],
                             ev["tags"], ev["content"]):
            return False
        return verify(bytes.fromhex(ev["id"]), ev["pubkey"], bytes.fromhex(ev["sig"]))
    except Exception:
        return False


def _jitter(now):
    return now - secrets.randbelow(172800)


def gift_wrap(sk, rumor, target_pub):
    now = int(time.time())
    seal = make_event(
        sk, 13,
        nip44_encrypt(_j(rumor), conv_key(sk, target_pub)),
        [], _jitter(now),
    )
    esk = new_sk()
    return make_event(
        esk, 1059,
        nip44_encrypt(_j(seal), conv_key(esk, target_pub)),
        [["p", target_pub]], _jitter(now),
    )


def unwrap(sk, wrap):
    if wrap.get("kind") != 1059:
        raise ValueError("not a gift wrap")
    me = pubhex(sk)
    tags = wrap.get("tags") or []
    if not any(t and t[0] == "p" and len(t) >= 2 and t[1] == me for t in tags):
        raise ValueError("wrap not addressed to us")
    seal = json.loads(nip44_decrypt(wrap["content"], conv_key(sk, wrap["pubkey"])))
    if seal.get("kind") != 13 or not verify_event(seal):
        raise ValueError("bad seal")
    r = json.loads(nip44_decrypt(seal["content"], conv_key(sk, seal["pubkey"])))
    if r.get("pubkey") != seal["pubkey"]:
        raise ValueError("sender mismatch")
    return r


# ══════════════════ NIP-05 / kind 0 ══════════════════
def nip05_resolve(session, ident):
    if "@" not in ident:
        return None
    local, domain = ident.split("@", 1)
    if not local or not domain:
        return None
    url = f"https://{domain}/.well-known/nostr.json?name={local}"
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        names = data.get("names") or {}
        pub = names.get(local) or names.get(local.lower())
        if pub and re.fullmatch(r"[0-9a-fA-F]{64}", pub):
            return pub.lower()
    except Exception:
        pass
    return None


def parse_kind0(content):
    try:
        d = json.loads(content)
        return {
            "name": d.get("name") or "",
            "display_name": d.get("display_name") or "",
            "about": d.get("about") or "",
            "picture": d.get("picture") or "",
            "nip05": d.get("nip05") or "",
        }
    except Exception:
        return {}


# ══════════════════ Tor ══════════════════
_KNOWN_SOCKS = (9050, 9150, 19050)


def find_tor():
    for p in _KNOWN_SOCKS:
        try:
            socket.create_connection(("127.0.0.1", p), 0.4).close()
            return p
        except OSError:
            pass
    return None


def _free_port(start=19050, tries=20):
    for p in range(start, start + tries):
        try:
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
    return None


class Tor:
    """Tor process manager with automatic transport fallback
    (Snowflake → WebTunnel → obfs4) for heavy censorship."""

    MODES = ("webtunnel", "snowflake", "obfs4")  # WebTunnel first — more reliable in Iran

    def __init__(self, datadir):
        self.dir = datadir
        self.proc = None
        self.progress = 0
        self.port = None
        self.err = ""
        self._reader = None
        self.mode = "snowflake"
        self._mode_idx = 0

    def _torrc_path(self):
        return Path(self.dir) / "torrc"

    def _write_mode(self, mode: str):
        try:
            from nyxchat.bootstrap import write_torrc
            r = write_torrc(mode)
            if r is None:
                self.err = f"no PT binary for mode {mode}"
                return False
            self.mode = mode
            return True
        except Exception as e:
            self.err = f"cannot write torrc for {mode}: {e}"
            return False

    def start(self, mode: str | None = None):
        if mode:
            self.mode = mode
            self._write_mode(mode)

        exe = shutil.which("tor")
        if not exe:
            self.err = "tor binary not found on PATH"
            return False
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as e:
            self.err = f"cannot create datadir: {e}"
            return False
        port = _free_port()
        if port is None:
            self.err = "no free port for tor"
            return False

        args = [exe, "--SocksPort", str(port),
                "--DataDirectory", self.dir,
                "--ClientOnly", "1",
                "--Log", "notice stdout"]

        torrc = self._torrc_path()
        if torrc.exists():
            args.extend(["-f", str(torrc)])
            print(f"[nyx] using torrc ({self.mode}): {torrc}", file=sys.stderr, flush=True)

        try:
            self.proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except Exception as e:
            self.err = f"tor launch failed: {e}"
            return False
        self.port = port
        self.progress = 0
        self.err = ""
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        return True

    def try_next_mode(self) -> bool:
        """Stop current Tor, switch to next transport, restart."""
        self.stop()
        self.progress = 0
        self.err = ""
        self._mode_idx += 1
        if self._mode_idx >= len(self.MODES):
            self.err = "all transport modes failed"
            return False
        next_mode = self.MODES[self._mode_idx]
        print(f"[nyx] switching transport → {next_mode}", file=sys.stderr, flush=True)
        if not self._write_mode(next_mode):
            # skip modes that can't be configured
            return self.try_next_mode()
        return self.start()

    def _read(self):
        if not (self.proc and self.proc.stdout):
            return
        for line in self.proc.stdout:
            m = re.search(r"Bootstrapped (\d+)%", line)
            if m:
                self.progress = int(m.group(1))
            elif ("[err]" in line or "[warn]" in line) and not self.err:
                self.err = line.strip()[:200]

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None


# ══════════════════ Relay ══════════════════
class Relay(threading.Thread):
    def __init__(self, url, node):
        super().__init__(daemon=True)
        self.url = url
        self.node = node
        self.state = "down"
        self.ws = None

    def send(self, msg):
        if self.state != "up" or not self.ws:
            return False
        try:
            self.ws.send(_j(msg))
            return True
        except Exception:
            self.state = "down"
            return False

    def run(self):
        back = 3
        while not self.node.closing:
            if self.node.port is None:
                time.sleep(1.0)
                continue
            try:
                self.state = "connecting"
                self.ws = websocket.create_connection(
                    self.url, timeout=40,
                    http_proxy_host="127.0.0.1",
                    http_proxy_port=self.node.port,
                    proxy_type="socks5h",
                )
                self.ws.settimeout(1)
                self.state = "up"
                back = 3
                last = time.time()
                for sid, filters in list(self.node.subs.items()):
                    self.send(["REQ", sid, *filters])
                for ev in list(self.node.pending.values()):
                    self.send(["EVENT", ev])
                while not self.node.closing:
                    try:
                        raw = self.ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.time() - last > 30:
                            try: self.ws.ping()
                            except Exception: raise ConnectionError
                            last = time.time()
                        continue
                    if not raw:
                        raise ConnectionError("closed")
                    self.node.handle(raw)
            except Exception as e:
                print(f"[nyx] relay {self.url}: {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
            self.state = "down"
            try:
                if self.ws: self.ws.close()
            except Exception:
                pass
            self.ws = None
            time.sleep(back)
            back = min(back * 2, 60)


# ══════════════════ Node ══════════════════
_SEEN_MAX = 10000


class Node:
    def __init__(self, sk, relays, out, seen=()):
        self.sk = sk
        self.pk = pubhex(sk)
        self.out = out
        self.relays = [Relay(u, self) for u in relays]
        self.subs = {}
        self.pending = {}
        self.closing = False
        self.port = None
        self.phase = "starting"
        self._err = ""
        self._seen = OrderedDict((s, None) for s in seen)
        self.inq = queue.Queue()
        home = os.environ.get("NYX_HOME") or os.path.expanduser("~/.nyx")
        self.tor = Tor(os.path.join(home, "tor"))
        threading.Thread(target=self._boot_tor, daemon=True).start()
        threading.Thread(target=self._work, daemon=True).start()

    def _boot_tor(self):
        p = find_tor()
        if p is not None:
            self.port = p
            self.phase = "ready"
            return
        # WebTunnel first (best for Iran DPI), then Snowflake, then obfs4
        if not self.tor.start("webtunnel"):
            self._err = self.tor.err or "tor failed to start"
            self.phase = "error"
            return
        self.phase = "bootstrapping"
        started = time.time()
        while not self.closing:
            prog = self.tor.progress
            elapsed = time.time() - started
            if prog >= 100:
                self.port = self.tor.port
                self.phase = "ready"
                print(f"[nyx] Tor ready via {self.tor.mode}", file=sys.stderr, flush=True)
                return
            if self.tor.proc and self.tor.proc.poll() is not None:
                if self.tor.try_next_mode():
                    started = time.time()
                    continue
                self._err = self.tor.err or "tor exited"
                self.phase = "error"
                return
            # Adaptive timeout: bail early if barely moving
            stuck = (
                (elapsed > 25 and prog < 15) or
                (elapsed > 45 and prog < 50) or
                (elapsed > 75 and prog < 90) or
                (elapsed > 100)
            )
            if stuck:
                print(f"[nyx] {self.tor.mode} stuck at {prog}% after {int(elapsed)}s, trying next…",
                      file=sys.stderr, flush=True)
                if self.tor.try_next_mode():
                    started = time.time()
                    continue
                self._err = self.tor.err or "all transports failed"
                self.phase = "error"
                return
            time.sleep(0.4)

    def status(self):
        up = sum(1 for r in self.relays if r.state == "up")
        mode = getattr(self.tor, "mode", "") or ""
        if self.phase == "ready":
            return f"Tor ({mode}) · {up}/{len(self.relays)} relays", True
        if self.phase == "bootstrapping":
            return f"Tor {mode} {self.tor.progress}%", False
        if self.phase == "error":
            return f"Tor: {self._err or 'error'}", False
        return "Tor starting…", False

    def start(self):
        for r in self.relays:
            r.start()

    def stop(self):
        self.closing = True
        self.tor.stop()

    def add_relay(self, url):
        if all(r.url != url for r in self.relays):
            r = Relay(url, self)
            self.relays.append(r)
            r.start()

    def handle(self, raw):
        try:
            m = json.loads(raw)
            if not isinstance(m, list) or not m:
                return
            if m[0] == "EVENT" and len(m) >= 3:
                self.inq.put(m[2])
            elif m[0] == "OK" and len(m) >= 3:
                if m[2] or (len(m) >= 4 and "duplicate" in str(m[3])):
                    self.pending.pop(m[1], None)
                    self.out.put(("ok", m[1]))
            elif m[0] == "EOSE" and len(m) >= 2:
                self.out.put(("eose", m[1]))
        except Exception:
            pass

    def _mark_seen(self, eid):
        if eid in self._seen:
            self._seen.move_to_end(eid)
            return False
        self._seen[eid] = None
        if len(self._seen) > _SEEN_MAX:
            self._seen.popitem(last=False)
        return True

    def _work(self):
        while not self.closing:
            try:
                ev = self.inq.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if not isinstance(ev, dict) or "id" not in ev:
                    continue
                if not self._mark_seen(ev["id"]):
                    continue
                k = ev.get("kind")
                if k == 1059:
                    try:
                        rumor = unwrap(self.sk, ev)
                    except Exception:
                        continue
                    self.out.put(("dm", rumor, ev["id"]))
                elif k == 0 and verify_event(ev):
                    pr = parse_kind0(ev.get("content", ""))
                    # prefer display_name for UI; keep name (username) as fallback
                    label = pr.get("display_name") or pr.get("name") or ""
                    self.out.put((
                        "profile",
                        ev["pubkey"],
                        label,
                        ev.get("created_at", 0),
                        pr,  # full profile dict for username matching
                    ))
                elif k == 1 and verify_event(ev):
                    self.out.put(("note", ev))
            except Exception:
                pass

    def subscribe(self, sid, filters):
        self.subs[sid] = filters
        for r in self.relays:
            r.send(["REQ", sid, *filters])

    def publish(self, ev):
        self.pending[ev["id"]] = ev
        for r in self.relays:
            r.send(["EVENT", ev])

    def send_rumor(self, others, kind, content, tags=()):
        others = sorted(set(p for p in others if p and p != self.pk))
        ts = int(time.time())
        tg = [["p", p] for p in (others or [self.pk])]
        tg += [list(t) for t in tags]
        rumor = dict(pubkey=self.pk, created_at=ts, kind=kind, tags=tg, content=content)
        rumor["id"] = ev_id(self.pk, ts, kind, tg, content)
        wids = []
        for target in others + [self.pk]:
            w = gift_wrap(self.sk, rumor, target)
            self.publish(w)
            wids.append(w["id"])
        return rumor, wids

    def post_note(self, text):
        ev = make_event(self.sk, 1, text, [["t", "nyx"]])
        self.publish(ev)
        return ev

    def set_profile(self, **fields):
        ev = make_event(self.sk, 0, _j(fields))
        self.publish(ev)
        return ev

    def session(self):
        if not self.port:
            raise RuntimeError("Tor not ready — refusing clearnet request")
        s = requests.Session()
        s.proxies = {k: f"socks5h://127.0.0.1:{self.port}" for k in ("http", "https")}
        return s


# ══════════════════ files (AES-GCM + Blossom) ══════════════════
class _Prog(io.BytesIO):
    def __init__(self, data, cb):
        super().__init__(data)
        self.cb = cb
        self.n = max(1, len(data))

    def read(self, size=-1):
        b = super().read(size)
        self.cb(min(1.0, self.tell() / self.n))
        return b


def encrypt_file(data):
    key, nonce = os.urandom(32), os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, data, None)
    return key.hex(), nonce.hex(), ct


def decrypt_file(ct, key_hex, nonce_hex):
    return AESGCM(bytes.fromhex(key_hex)).decrypt(bytes.fromhex(nonce_hex), ct, None)


def upload(session, servers, ct, cb):
    h = hashlib.sha256(ct).hexdigest()
    err = "no servers"
    for srv in servers:
        try:
            auth = make_event(
                new_sk(), 24242, "Upload",
                [["t", "upload"], ["x", h],
                 ["expiration", str(int(time.time()) + 600)]],
            )
            hdr = {
                "Authorization": "Nostr " + base64.b64encode(_j(auth).encode()).decode(),
                "Content-Type": "application/octet-stream",
                "X-SHA-256": h,
            }
            r = session.put(srv.rstrip("/") + "/upload",
                            data=_Prog(ct, cb), headers=hdr, timeout=600)
            if r.status_code in (200, 201):
                url = r.json().get("url") or srv.rstrip("/") + "/" + h
                return url, h
            err = f"{srv}: HTTP {r.status_code} {r.headers.get('X-Reason', '')}"
        except Exception as e:
            err = f"{srv}: {str(e)[:80]}"
    raise RuntimeError(err)


def download(session, url, cb, expected_sha256=None):
    r = session.get(url, stream=True, timeout=120)
    r.raise_for_status()
    total = int(r.headers.get("Content-Length") or 0)
    buf = bytearray()
    for ch in r.iter_content(65536):
        buf += ch
        if total:
            cb(len(buf) / total)
    data = bytes(buf)
    if expected_sha256:
        if hashlib.sha256(data).hexdigest() != expected_sha256.lower():
            raise ValueError("sha256 mismatch")
    return data
