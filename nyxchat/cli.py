"""Nyx terminal UI."""
import os, sys, json, time, queue, threading, re, secrets, textwrap, hashlib
from pathlib import Path

from nyxchat import bootstrap

# Fill these in from core after bootstrap succeeds
Node = pubhex = new_sk = nsec = npub = parse_pub = short = bech_dec = None
make_event = verify_event = nip05_resolve = parse_kind0 = None
encrypt_file = decrypt_file = upload = download = None
visual = _is_rtl = None


def _load():
    global Node, pubhex, new_sk, nsec, npub, parse_pub, short, bech_dec
    global make_event, verify_event, nip05_resolve, parse_kind0
    global encrypt_file, decrypt_file, upload, download, visual, _is_rtl
    from nyxchat import core
    for k in ("Node", "new_sk", "pubhex", "npub", "nsec", "parse_pub",
              "short", "bech_dec", "make_event", "verify_event",
              "nip05_resolve", "parse_kind0",
              "encrypt_file", "decrypt_file", "upload", "download"):
        globals()[k] = getattr(core, k)
    from nyxchat.shaping import visual as v, is_rtl as r
    visual = v
    _is_rtl = r


HOME = bootstrap.HOME
RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.snort.social",
    "wss://relay.primal.net",
]
BLOSSOM_SERVERS = [
    "https://blossom.primal.net",
    "https://blossom.band",
    "https://files.sovbit.host",
]
NIP05_DOMAINS = [
    "nostrcheck.me",
    "nostrplebs.com",
    "iris.to",
    "coracle.social",
    "nostr.band",
]
MAX_FILE = 50 * 1024 * 1024


def _j(o):
    return json.dumps(o, separators=(",", ":"), ensure_ascii=False)


def format_size(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


# ── lightweight markdown-like formatting ──────────────────────────────
# Supports: **bold**  *italic*  `code`  ```code block```  ~~strike~~
# Returns list of (style, text)

_FMT_RE = re.compile(
    r"```(?P<codeblock>[\s\S]*?)```"
    r"|`(?P<code>[^`\n]+)`"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|__(?P<bold2>.+?)__"
    r"|(?<!\*)\*(?P<italic>[^*]+?)\*(?!\*)"
    r"|(?<!_)_(?P<italic2>[^_]+?)_(?!_)"
    r"|~~(?P<strike>.+?)~~",
    re.DOTALL,
)


def parse_fmt(text: str):
    """Parse message text into styled segments (logical order)."""
    if not text:
        return [("plain", "")]
    out = []
    pos = 0
    for m in _FMT_RE.finditer(text):
        if m.start() > pos:
            out.append(("plain", text[pos:m.start()]))
        if m.group("codeblock") is not None:
            out.append(("codeblock", m.group("codeblock").strip("\n")))
        elif m.group("code") is not None:
            out.append(("code", m.group("code")))
        elif m.group("bold") is not None or m.group("bold2") is not None:
            out.append(("bold", m.group("bold") or m.group("bold2")))
        elif m.group("italic") is not None or m.group("italic2") is not None:
            out.append(("italic", m.group("italic") or m.group("italic2")))
        elif m.group("strike") is not None:
            out.append(("strike", m.group("strike")))
        pos = m.end()
    if pos < len(text):
        out.append(("plain", text[pos:]))
    return out or [("plain", text)]


def A(n):
    import curses
    return curses.color_pair(n)


# ══════════════════════ themes ══════════════════════
THEMES = [
    ("Midnight", 234, 233, 252, 244, 75, 25, 237, 238, 255, [39, 45, 51, 87, 123, 159]),
    ("Aurora",   17,  16, 253, 245, 49, 30,  23,  24, 255, [46, 48, 50, 45, 63, 99]),
    ("Sakura",  224, 225,  52, 138, 205, 211, 255, 218, 16, [199, 205, 211, 175, 169, 163]),
    ("Matrix",    0, 232,  46,  28,  46,  22, 234,  22,  46, [22, 28, 34, 40, 46, 82]),
    ("Sunset",  235,  52, 223, 138, 208, 130, 238,  94, 255, [196, 202, 208, 214, 220, 226]),
    ("Forest",   22,  22, 250, 242, 77, 22, 235,  22, 250, [28, 34, 40, 46, 82, 118]),
]
LOGO = [
    "███╗   ██╗██╗   ██╗██╗  ██╗",
    "████╗  ██║╚██╗ ██╔╝╚██╗██╔╝",
    "██╔██╗ ██║ ╚████╔╝  ╚███╔╝ ",
    "██║╚██╗██║  ╚██╔╝   ██╔██╗ ",
    "██║ ╚████║   ██║   ██╔╝ ██╗",
    "╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝",
]


# ══════════════════════ App ══════════════════════
class App:
    def __init__(self, scr):
        import curses
        self.curses = curses
        self.s = scr
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        curses.start_color()
        self.s.keypad(True)
        self.s.timeout(60)

        self.frame = 0
        self.theme_i = 0
        self.set_theme(0)

        self.sk = self.pub = self.node = None
        self.profile = {}
        self.contacts = {}
        self.aliases = {}
        self.groups = {}   # gid -> {id, name, type, members, owner, msgs, ...}
        self.chats = []
        self.cur = None
        self.buf = ""
        self.scroll = 0
        self.sel_y = 5.0
        self.open_t = -99
        self.toast = None
        self.curtain = None
        self.sent_ids = set()

        # async transfer state
        self.upload_progress = None
        self.upload_name = ""
        self.upload_result = None
        self.upload_error = None
        self.download_progress = None
        self.download_name = ""
        self.download_result = None
        self.download_error = None

    # ---------- theme ----------
    def set_theme(self, i):
        curses = self.curses
        self.theme_i = i % len(THEMES)
        _, bg, side, fg, dim, acc, mine, their, sel, bfg, grad = THEMES[self.theme_i]
        ip = curses.init_pair
        ip(1, fg, bg); ip(2, fg, side); ip(3, acc, bg); ip(4, bfg, mine); ip(5, fg, their)
        ip(7, dim, bg); ip(8, fg, sel); ip(9, side, acc); ip(10, dim, side); ip(11, acc, sel)
        ip(12, acc, side); ip(13, mine, bg); ip(14, their, bg)
        # modal palette (independent of theme)
        ip(15, 252, 236)   # body
        ip(16, 245, 236)   # dim text
        ip(17, 75,  236)   # accent border/title
        ip(18, 233, 233)   # shadow
        ip(19, 255, 236)   # input text
        ip(20, 234, 236)   # file bg
        # formatting styles (work on both message bubbles)
        ip(21, 229, mine)  # code on mine bubble (warm mono)
        ip(22, 229, their) # code on their bubble
        ip(23, 255, 236)   # codeblock bg (modal-ish)
        ip(24, 244, 236)   # codeblock text
        for j, g in enumerate(grad):
            ip(30 + j, g, side); ip(40 + j, side, g); ip(50 + j, g, bg)
        self.s.bkgd(" ", A(1))

    def safe(self, y, x, text, attr=0):
        curses = self.curses
        h, w = self.s.getmaxyx()
        if y < 0 or y >= h or x >= w or not text:
            return
        if x < 0:
            text, x = text[-x:], 0
        text = text[: max(0, w - x)]
        if y == h - 1 and x + len(text) >= w:
            text = text[: w - x - 1]
        try:
            self.s.addstr(y, x, text, attr)
        except curses.error:
            pass

    def toast_msg(self, txt):
        self.toast = (txt, self.frame, self.frame + 55)

    def now(self):
        return time.strftime("%H:%M")

    # ---------- profile ----------
    def load_profile(self):
        f = HOME / "profile.json"
        if f.exists():
            try:
                self.profile = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                self.profile = {}

    def save_profile(self):
        (HOME / "profile.json").write_text(_j(self.profile), encoding="utf-8")

    def publish_profile(self):
        if not self.node:
            return
        data = {k: v for k, v in self.profile.items()
                if k in ("name", "display_name", "about", "nip05") and v}
        if not data:
            return
        self.node.publish(make_event(self.sk, 0, _j(data)))

    def _valid_username(self, u):
        """Telegram-like: 5-32 chars, a-z 0-9 underscore."""
        u = (u or "").strip().lstrip("@").lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", u):
            return None
        return u

    def set_name_dialog(self):
        cur = self.profile.get("display_name", "") or self.profile.get("name", "")
        new = self.modal_input("Your name", "How should others see you?", default=cur)
        if new is None or not new.strip():
            return
        new = new.strip()
        self.profile["display_name"] = new
        self.save_profile()
        self.publish_profile()
        self.toast_msg("Name saved")

    def set_username_dialog(self):
        cur = self.profile.get("name", "") or self.profile.get("nip05", "")
        if "@" in cur:
            cur = cur.split("@", 1)[0]
        new = self.modal_input(
            "Username",
            "@username  ·  5–32 letters, numbers, _",
            default=cur, allow_empty=True,
        )
        if new is None:
            return
        new = new.strip().lstrip("@")
        if not new:
            self.profile["name"] = ""
            self.profile.pop("nip05", None)
            self.save_profile()
            self.publish_profile()
            self.toast_msg("Username cleared")
            return
        u = self._valid_username(new)
        if not u:
            self.toast_msg("Invalid: use a-z, 0-9, _ (min 5 chars)")
            return
        self.profile["name"] = u
        # keep optional NIP-05 if user set a full name@domain before
        if self.profile.get("nip05") and "@" in self.profile["nip05"]:
            pass
        self.save_profile()
        self.publish_profile()
        self.toast_msg(f"Username set to @{u}")

    def onboarding(self):
        """First-run Telegram-like setup: name + @username."""
        if self.profile.get("display_name") and self.profile.get("name"):
            return
        name = self.modal_input(
            "Welcome to Nyx",
            "Your display name (like Telegram)",
            default=self.profile.get("display_name", ""),
        )
        if name and name.strip():
            self.profile["display_name"] = name.strip()
        uname = self.modal_input(
            "Choose username",
            "@username  ·  others can find you with this",
            default=self.profile.get("name", ""),
            allow_empty=True,
        )
        if uname and uname.strip():
            u = self._valid_username(uname)
            if u:
                self.profile["name"] = u
            else:
                self.toast_msg("Username skipped (invalid format)")
        self.save_profile()
        self.publish_profile()
        shown = self.profile.get("name")
        if shown:
            self.toast_msg(f"You're @{shown}")
        elif self.profile.get("display_name"):
            self.toast_msg("Profile saved")

    # ---------- account ----------
    def load_or_create_account(self):
        env = os.environ.get("NYX_NSEC", "").strip()
        f = HOME / "key"
        raw = env or (f.read_text(encoding="utf-8").strip() if f.exists() else "")
        if raw:
            try:
                self._set_key(raw)
                return True
            except Exception:
                pass
        return self.first_run_dialog()

    def _set_key(self, raw):
        raw = raw.strip()
        if raw.startswith("nsec1"):
            self.sk = int.from_bytes(bech_dec(raw)[1], "big")
        elif re.fullmatch(r"[0-9a-fA-F]{64}", raw):
            self.sk = int(raw, 16)
        else:
            raise ValueError("bad key")
        self.pub = pubhex(self.sk)
        (HOME / "key").write_text(nsec(self.sk), encoding="utf-8")
        try:
            os.chmod(HOME / "key", 0o600)
        except OSError:
            pass

    def first_run_dialog(self):
        curses = self.curses
        msg = "No identity found."
        while True:
            self.frame += 1
            self.draw()
            h, w = self.s.getmaxyx()
            bw, bh = 60, 12
            y0 = max(1, (h - bh) // 2)
            x0 = max(2, (w - bw) // 2)
            self._paint_panel(y0, x0, bw, bh, " Welcome to Nyx ")
            self.safe(y0 + 2, x0 + 3, msg, A(16))
            self.safe(y0 + 4, x0 + 3, "Choose:", A(16))
            self.safe(y0 + 5, x0 + 5, "[G]  Generate new identity", A(19) | curses.A_BOLD)
            self.safe(y0 + 6, x0 + 5, "[P]  Paste nsec / hex key", A(19) | curses.A_BOLD)
            self.safe(y0 + 7, x0 + 5, "[Q]  Quit", A(16))
            self.safe(y0 + bh - 2, x0 + 3, "press a key…", A(16))
            self.s.refresh()
            try:
                k = self.s.get_wch()
            except curses.error:
                continue
            if not isinstance(k, str):
                continue
            if k in ("q", "Q", "\x1b", "\x03"):
                return False
            if k in ("g", "G"):
                self.sk = new_sk()
                self.pub = pubhex(self.sk)
                (HOME / "key").write_text(nsec(self.sk), encoding="utf-8")
                try:
                    os.chmod(HOME / "key", 0o600)
                except OSError:
                    pass
                self.toast_msg("New identity generated")
                return True
            if k in ("p", "P"):
                raw = self.modal_input("Paste key", "nsec or 64-char hex")
                if raw is None:
                    continue
                try:
                    self._set_key(raw)
                    return True
                except Exception as e:
                    msg = "Invalid: " + str(e)[:40]

    # ---------- contacts ----------
    def load_contacts(self):
        f = HOME / "contacts.json"
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                self.aliases = {k.lower(): v for k, v in data.get("aliases", {}).items()}
                for pub in data.get("pubs", []):
                    try:
                        parse_pub(pub)
                    except Exception:
                        continue
                    self._add_contact(pub, save=False)
            except Exception:
                pass
        self.refresh_chats()
        if self.node and self.contacts:
            self.node.subscribe("profiles",
                                [{"kinds": [0], "authors": list(self.contacts.keys())}])

    def save_contacts(self):
        (HOME / "contacts.json").write_text(
            _j({"pubs": list(self.contacts.keys()), "aliases": self.aliases}),
            encoding="utf-8")

    # ---------- groups & channels ----------
    def load_groups(self):
        f = HOME / "groups.json"
        if not f.exists():
            return
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for g in data.get("groups", []):
                gid = g.get("id")
                if not gid:
                    continue
                g.setdefault("msgs", [])
                g.setdefault("unread", 0)
                g.setdefault("flash", 0)
                g.setdefault("last_t", 0)
                g.setdefault("order", 0)
                g.setdefault("k", sum(map(ord, gid)) % 6)
                g.setdefault("type", "group")
                g.setdefault("members", [])
                g.setdefault("owner", self.pub or "")
                # mark as group chat object
                g["pub"] = gid  # pseudo-pub for routing
                g["is_group"] = True
                self.groups[gid] = g
            self.refresh_chats()
        except Exception:
            pass

    def save_groups(self):
        out = []
        for g in self.groups.values():
            out.append({
                "id": g["id"],
                "name": g.get("name", ""),
                "type": g.get("type", "group"),
                "members": g.get("members", []),
                "owner": g.get("owner", ""),
            })
        (HOME / "groups.json").write_text(_j({"groups": out}), encoding="utf-8")

    def _new_gid(self):
        return secrets.token_hex(8)

    def create_group_dialog(self, as_channel=False):
        kind = "channel" if as_channel else "group"
        title = "New channel" if as_channel else "New group"
        name = self.modal_input(title, "Name")
        if not name or not name.strip():
            return
        name = name.strip()
        members_raw = self.modal_input(
            "Members",
            "@user · npub · comma-separated  ·  Enter to skip",
            allow_empty=True,
        )
        members = []
        if members_raw and members_raw.strip():
            for part in re.split(r"[,\s]+", members_raw.strip()):
                part = part.strip()
                if not part:
                    continue
                pub = None
                try:
                    if part.startswith("@"):
                        n = part[1:].lower()
                        pub = self.aliases.get(n)
                        if not pub:
                            pub, _ = self.resolve_username(n)
                    elif "@" in part and not part.startswith("npub"):
                        pub = self.resolve_nip05(part)
                    else:
                        pub = parse_pub(part)
                except Exception:
                    pub = None
                if pub and pub != self.pub:
                    members.append(pub.lower())
                    self._add_contact(pub)
        gid = self._new_gid()
        g = dict(
            id=gid, name=name, type=kind,
            members=sorted(set(members)),
            owner=(self.pub or "").lower(),
            msgs=[], unread=0, flash=0, last_t=0, order=time.time(),
            k=sum(map(ord, gid)) % 6,
            pub=gid, is_group=True,
        )
        self.groups[gid] = g
        self.save_groups()
        self.refresh_chats()
        self.open_chat(g)
        icon = "📢" if as_channel else "👥"
        self.toast_msg(f"{icon} {name} created")

    def add_group_member_dialog(self):
        c = self.cur
        if not c or not c.get("is_group"):
            self.toast_msg("Open a group/channel first")
            return
        if c.get("type") == "channel" and c.get("owner") != (self.pub or "").lower():
            self.toast_msg("Only channel owner can add members")
            return
        raw = self.modal_input("Add member", "@user · npub · name@domain")
        if not raw:
            return
        raw = raw.strip()
        pub = None
        try:
            if raw.startswith("@"):
                n = raw[1:].lower()
                pub = self.aliases.get(n)
                if not pub:
                    pub, _ = self.resolve_username(n)
            elif "@" in raw and not raw.startswith("npub"):
                pub = self.resolve_nip05(raw)
            else:
                pub = parse_pub(raw)
        except Exception as e:
            self.toast_msg(str(e)[:40])
            return
        if not pub:
            self.toast_msg("User not found")
            return
        pub = pub.lower()
        if pub == (self.pub or "").lower():
            self.toast_msg("That's you")
            return
        mem = c.setdefault("members", [])
        if pub in mem:
            self.toast_msg("Already a member")
            return
        mem.append(pub)
        self._add_contact(pub)
        self.save_groups()
        self.toast_msg("Member added")

    def _add_contact(self, pub, name=None, save=True):
        pub = pub.lower()
        if pub in self.contacts:
            if name:
                self.contacts[pub]["name"] = name
            return self.contacts[pub]
        c = dict(pub=pub, name=name or short(pub),
                 msgs=[], unread=0, flash=0, last_t=0,
                 k=sum(map(ord, pub)) % 6, order=0)
        self.contacts[pub] = c
        self.refresh_chats()
        if save:
            self.save_contacts()
        return c

    def resolve_nip05(self, ident):
        if not self.node:
            return None
        try:
            s = self.node.session()
        except Exception as e:
            self.toast_msg(f"Tor not ready: {e}")
            return None
        pub = nip05_resolve(s, ident)
        if not pub:
            self.toast_msg(f"NIP-05 lookup failed for {ident}")
        return pub

    def resolve_username(self, name):
        for dom in NIP05_DOMAINS:
            ident = f"{name}@{dom}"
            if not self.node:
                return None, None
            try:
                s = self.node.session()
            except Exception as e:
                self.toast_msg(f"Tor: {e}")
                return None, None
            pub = nip05_resolve(s, ident)
            if pub:
                return pub, ident
        return None, None

    def open_add_contact(self):
        raw = self.modal_input(
            "Add contact",
            "npub · hex · @name · name@domain · name=npub1…")
        if not raw:
            return
        raw = raw.strip()
        try:
            if "=" in raw:
                a, _, v = raw.partition("=")
                a = a.strip().lstrip("@").lower()
                if not a:
                    raise ValueError("empty alias")
                pub = parse_pub(v.strip())
                self.aliases[a] = pub
                self._add_contact(pub, name=a)
                self.save_contacts()
                self.toast_msg(f"alias @{a} saved")
            elif raw.startswith("@"):
                name = raw[1:].strip().lower()
                if not name:
                    raise ValueError("empty name")
                pub = self.aliases.get(name)
                ident = f"@{name}"
                if not pub:
                    self.toast = (f"resolving @{name}…", self.frame, self.frame + 3000)
                    self.draw()
                    self.s.refresh()
                    pub, ident2 = self.resolve_username(name)
                    if pub:
                        ident = "@" + ident2.split("@")[0]
                if not pub:
                    raise ValueError(f"@{name} not found")
                self.aliases[name] = pub
                self._add_contact(pub, name=name)
                self.save_contacts()
                self.toast_msg(f"added {ident}")
            elif "@" in raw and not raw.startswith("npub"):
                pub = self.resolve_nip05(raw)
                if not pub:
                    raise ValueError("NIP-05 lookup failed")
                local = raw.split("@", 1)[0].lower()
                self.aliases[local] = pub
                self._add_contact(pub, name=local)
                self.save_contacts()
                self.toast_msg(f"added {raw}")
            else:
                self._add_contact(parse_pub(raw))
                self.toast_msg("Contact added")
            if self.node:
                self.node.subscribe("profiles",
                                    [{"kinds": [0], "authors": list(self.contacts.keys())}])
            self.refresh_chats()
            if self.chats:
                self.open_chat(self.chats[0])
        except Exception as e:
            self.toast_msg("add failed: " + str(e)[:60])

    # ---------- chat helpers ----------
    def refresh_chats(self):
        items = list(self.contacts.values()) + list(self.groups.values())
        self.chats = sorted(items, key=lambda c: (-c.get("order", 0), c.get("name", "")))
        if self.cur is None and self.chats:
            self.cur = self.chats[0]

    def open_chat(self, chat):
        if chat is None or chat is self.cur:
            return
        self.cur = chat
        chat["unread"] = 0
        self.scroll = 0
        self.open_t = self.frame
        self.buf = ""

    def append_msg(self, chat, pub, text, mine=False, ts=None, file_info=None):
        t = time.strftime("%H:%M", time.localtime(ts or time.time()))
        chat["msgs"].append(dict(pub=pub, text=text, mine=mine, t=t, file_info=file_info))
        chat["order"] = time.time()
        if chat is self.cur:
            self.scroll = 0
        else:
            chat["unread"] += 1
            chat["flash"] = 30
        self.refresh_chats()

    def move(self, d):
        if not self.chats:
            return
        i = self.chats.index(self.cur) if self.cur in self.chats else 0
        self.open_chat(self.chats[(i + d) % len(self.chats)])

    # ---------- node events ----------
    def poll_node(self):
        if not self.node:
            return
        for _ in range(80):
            try:
                msg = self.node.out.get_nowait()
            except queue.Empty:
                break
            try:
                self.handle_node(msg)
            except Exception as e:
                self.toast_msg("err: " + str(e)[:40])

    def handle_node(self, msg):
        k = msg[0]
        if k == "dm":
            _, rumor, _wid = msg
            self.on_dm(rumor)
        elif k == "profile":
            _, pub, name, _ts = msg
            if pub in self.contacts and name:
                self.contacts[pub]["name"] = name
                self.refresh_chats()

    def on_dm(self, rumor):
        sender = rumor.get("pubkey", "")
        rid = rumor.get("id", "")
        if not sender:
            return
        if sender == self.pub and rid in self.sent_ids:
            return
        content = rumor.get("content", "")
        if rumor.get("kind") != 14 or not content:
            return
        # group/channel message?
        tags = rumor.get("tags") or []
        gid = None
        gname = None
        gtype = "group"
        for t in tags:
            if len(t) >= 2 and t[0] == "g":
                gid = t[1]
            elif len(t) >= 2 and t[0] == "subject":
                gname = t[1]
            elif len(t) >= 2 and t[0] == "nyx":
                gtype = t[1]
        if gid:
            g = self.groups.get(gid)
            if not g:
                g = dict(
                    id=gid, name=gname or gid[:8], type=gtype,
                    members=[], owner=sender.lower(),
                    msgs=[], unread=0, flash=0, last_t=0, order=0,
                    k=sum(map(ord, gid)) % 6, pub=gid, is_group=True,
                )
                self.groups[gid] = g
                self.save_groups()
            c = g
        else:
            c = self.contacts.get(sender) or self._add_contact(sender)
        c["last_t"] = time.time()
        c["order"] = time.time()
        if content.startswith("NYXFILE:"):
            try:
                info = json.loads(content[8:])
                txt = f"📎 {info['name']} ({format_size(info['size'])})"
                self.append_msg(c, sender, txt, mine=False,
                                ts=rumor.get("created_at"), file_info=info)
                self.refresh_chats()
                return
            except Exception:
                pass
        self.append_msg(c, sender, content, mine=False, ts=rumor.get("created_at"))
        self.refresh_chats()

    # ---------- start ----------
    def start_node(self):
        self.node = Node(self.sk, RELAYS, queue.Queue())
        self.node.start()
        self.node.subscribe("dm", [{"kinds": [1059], "#p": [self.pub]}])
        self.load_contacts()
        self.publish_profile()

    # ---------- file transfer ----------
    def send_file_dialog(self):
        if not self.cur:
            self.toast_msg("no chat open")
            return
        path = self.modal_input("Send file", "absolute or relative path")
        if not path:
            return
        path = path.strip().strip('"').strip("'")
        p = Path(path).expanduser()
        if not p.exists() or not p.is_file():
            self.toast_msg("file not found")
            return
        data = p.read_bytes()
        if len(data) > MAX_FILE:
            self.toast_msg(f"file too large ({format_size(len(data))}, max {format_size(MAX_FILE)})")
            return
        chat = self.cur
        name = p.name
        self.upload_progress = 0.0
        self.upload_name = name
        self.upload_result = None
        self.upload_error = None

        def worker():
            try:
                key, nonce, ct = encrypt_file(data)
                sha = hashlib.sha256(ct).hexdigest()
                session = self.node.session()

                def cb(p_):
                    self.upload_progress = p_

                url, _ = upload(session, BLOSSOM_SERVERS, ct, cb)
                info = dict(url=url, key=key, nonce=nonce,
                            name=name, size=len(data), sha256=sha)
                payload = "NYXFILE:" + json.dumps(info)
                rumor, _ = self.node.send_rumor([chat["pub"]], 14, payload)
                self.sent_ids.add(rumor["id"])
                self.upload_result = (chat, info)
            except Exception as e:
                self.upload_error = str(e)[:80]

        threading.Thread(target=worker, daemon=True).start()
        self.toast_msg(f"uploading {name}…")

    def download_last_file(self):
        if not self.cur:
            return
        for m in reversed(self.cur["msgs"]):
            if m.get("file_info"):
                self._start_download(m["file_info"])
                return
        self.toast_msg("no file in this chat")

    def _start_download(self, info):
        self.download_progress = 0.0
        self.download_name = info["name"]
        self.download_result = None
        self.download_error = None
        self.toast_msg(f"downloading {info['name']}…")

        def worker():
            try:
                session = self.node.session()

                def cb(p):
                    self.download_progress = p

                ct = download(session, info["url"], cb, info.get("sha256"))
                data = decrypt_file(ct, info["key"], info["nonce"])
                outdir = Path.home() / "Downloads" / "nyx"
                outdir.mkdir(parents=True, exist_ok=True)
                out = outdir / info["name"]
                i = 1
                while out.exists():
                    out = outdir / f"{Path(info['name']).stem} ({i}){Path(info['name']).suffix}"
                    i += 1
                out.write_bytes(data)
                self.download_result = str(out)
            except Exception as e:
                self.download_error = str(e)[:80]

        threading.Thread(target=worker, daemon=True).start()

    def check_transfers(self):
        if self.upload_error:
            self.toast_msg("upload failed: " + self.upload_error)
            self.upload_error = None
            self.upload_progress = None
        if self.upload_result:
            chat, info = self.upload_result
            self.upload_result = None
            self.upload_progress = None
            txt = f"📎 {info['name']} ({format_size(info['size'])})"
            self.append_msg(chat, self.pub, txt, mine=True, file_info=info)
            self.toast_msg(f"sent {info['name']}")
        if self.download_error:
            self.toast_msg("download failed: " + self.download_error)
            self.download_error = None
            self.download_progress = None
        if self.download_result:
            self.toast_msg("saved to " + self.download_result)
            self.download_result = None
            self.download_progress = None

    # ---------- tick ----------
    def tick(self):
        f = self.frame
        if self.cur in self.chats:
            self.sel_y += ((5 + 2 * self.chats.index(self.cur)) - self.sel_y) * 0.35
        for c in self.chats:
            if c["flash"]:
                c["flash"] -= 1
        if self.curtain is not None:
            p = f - self.curtain
            if p == 8:
                self.set_theme(self.theme_i + 1)
                self.toast_msg("Theme: " + THEMES[self.theme_i][0])
            if p >= 16:
                self.curtain = None
        self.poll_node()
        self.check_transfers()

    # ---------- draw ----------
    def draw(self):
        s = self.s
        s.erase()
        h, w = s.getmaxyx()
        if h < 14 or w < 56:
            self.safe(0, 0, "Terminal too small (min 56x14)", A(1))
            s.refresh()
            return
        sw = min(36, max(24, w // 3))
        self.draw_side(h, sw)
        self.draw_pane(h, w, sw)
        self.draw_overlay(h, w)
        s.refresh()

    def draw_side(self, h, sw):
        f = self.frame
        for y in range(h):
            self.safe(y, 0, " " * sw, A(2))
            self.safe(y, sw - 1, "│", A(10))
        self.safe(0, 2, "◆", A(12) | self.curses.A_BOLD)
        for i, ch in enumerate("NYX"):
            self.safe(0, 4 + i, ch, A(30 + (i - f // 4) % 6) | self.curses.A_BOLD)
        # own display name
        me = self.profile.get("display_name") or ""
        uname = self.profile.get("name") or ""
        if me:
            self.safe(1, 2, visual(me)[: sw - 4], A(3) | self.curses.A_BOLD)
        else:
            self.safe(1, 2, "set name (Ctrl+E)", A(7))
        if uname:
            label = "@" + uname.split("@")[0]
        elif self.pub:
            label = "npub " + short(self.pub)
        else:
            label = "set @user (Ctrl+U)"
        self.safe(2, 2, visual(label)[: sw - 4], A(7))
        # separator
        self.safe(3, 0, "─" * (sw - 1), A(10))
        if not self.chats:
            self.safe(5, 2, "No chats yet", A(7))
            self.safe(6, 2, "Ctrl+N to add", A(7))
        band = int(round(self.sel_y))
        for i, c in enumerate(self.chats):
            cy = 5 + i * 2
            if cy + 1 >= h - 2:
                break
            sel = band <= cy <= band + 1
            base = A(8) if sel else A(2)
            for yy in (cy, cy + 1):
                self.safe(yy, 0, " " * (sw - 1), base)
            if sel:
                self.safe(cy, 0, "▌", A(11) | self.curses.A_BOLD)
            self.safe(cy, 1, "    ", A(40 + c.get("k", 0)))
            if c.get("is_group"):
                badge = "CH" if c.get("type") == "channel" else "GR"
                self.safe(cy, 1, badge.center(4),
                          A(40 + c.get("k", 0)) | self.curses.A_BOLD)
            else:
                self.safe(cy, 1, c["name"][:2].upper().center(4),
                          A(40 + c.get("k", 0)) | self.curses.A_BOLD)
            if c.get("is_group"):
                prefix = "# " if c.get("type") == "channel" else "· "
            else:
                hot = c.get("last_t") and time.time() - c["last_t"] < 300
                prefix = ("● " if hot else "○ ")
            self.safe(cy, 6, prefix + visual(c["name"])[: sw - 12],
                      base | self.curses.A_BOLD)
            if c["msgs"]:
                last = c["msgs"][-1]
                pv = ("You: " if last["mine"] else "") + last["text"]
                self.safe(cy + 1, 6, visual(pv)[: sw - 12], base | self.curses.A_DIM)
            if c["unread"]:
                blink = c["flash"] and (c["flash"] // 3) % 2
                self.safe(cy + 1, sw - 4 - len(str(c["unread"])),
                          " %d " % c["unread"],
                          (A(12) if blink else A(9)) | self.curses.A_BOLD)
        # status bar
        st, ready = self.node.status() if self.node else ("starting…", False)
        # transfer status
        if self.upload_progress is not None:
            st = f"⬆ {int(self.upload_progress * 100)}%  {self.upload_name[:16]}"
            ready = False
        elif self.download_progress is not None:
            st = f"⬇ {int(self.download_progress * 100)}%  {self.download_name[:16]}"
            ready = False
        self.safe(h - 1, 2, st[: sw - 3], A(3) if ready else A(7))

    def _style_attr(self, style, mine, base_attr):
        """Map format style name → curses attribute."""
        c = self.curses
        if style == "bold":
            return base_attr | c.A_BOLD
        if style == "italic":
            # A_ITALIC not available on all terminals
            try:
                return base_attr | c.A_ITALIC
            except AttributeError:
                return base_attr | c.A_UNDERLINE
        if style == "code":
            return A(21 if mine else 22) | c.A_BOLD
        if style == "strike":
            return base_attr | c.A_DIM
        if style == "codeblock":
            return A(24) | c.A_DIM
        return base_attr

    def build(self, c, pw):
        maxw = max(16, int((pw - 6) * 0.72))
        lines = []
        prev = None
        for m in c["msgs"]:
            side = "R" if m["mine"] else "L"
            mine = m["mine"]
            if prev is not None and prev != m["pub"]:
                lines.append(("L", [("", 0)]))
            if prev != m["pub"]:
                # subtle timestamp + name separator
                ts = m.get("t") or ""
                lines.append((side, [(ts, A(7) | self.curses.A_DIM)]))
            prev = m["pub"]

            if m.get("file_info"):
                ba, bp = (A(4), 20) if mine else (A(5), 20)
            else:
                ba, bp = (A(4), 13) if mine else (A(5), 14)

            # Parse formatting on logical text, then shape each segment
            segments = parse_fmt(m["text"])
            # Flatten into display lines with attributes
            # First expand codeblocks into their own lines
            flat = []
            for style, chunk in segments:
                if style == "codeblock":
                    for cl in (chunk.splitlines() or [""]):
                        flat.append(("codeblock", cl))
                else:
                    flat.append((style, chunk))

            # Now wrap while preserving styles
            # Build visual runs
            runs = []
            for style, chunk in flat:
                if not chunk and style != "codeblock":
                    continue
                vis = visual(chunk) if chunk else ""
                attr = self._style_attr(style, mine, ba)
                runs.append((vis, attr, style))

            # Word-wrap the runs into lines of maxw-2
            # Simple approach: join plain text for wrapping, then re-apply
            # For better quality we wrap per-run when possible
            wrapped_rows = []
            current_row = []
            current_len = 0

            def flush_row():
                nonlocal current_row, current_len
                if current_row:
                    wrapped_rows.append(current_row)
                current_row = []
                current_len = 0

            for vis, attr, style in runs:
                if style == "codeblock":
                    flush_row()
                    # code blocks get their own full-width lines
                    for part in textwrap.wrap(vis, maxw - 4) or [""]:
                        wrapped_rows.append([(part, attr, "codeblock")])
                    continue
                # split into words keeping spaces
                parts = re.findall(r"\S+|\s+", vis) or [""]
                for part in parts:
                    plen = len(part)
                    if current_len + plen > maxw - 2 and current_row:
                        flush_row()
                    current_row.append((part, attr, style))
                    current_len += plen
            flush_row()

            if not wrapped_rows:
                wrapped_rows = [[("", ba, "plain")]]

            inner = max(
                (sum(len(t) for t, _, _ in row) for row in wrapped_rows),
                default=1,
            )
            inner = max(inner, 4)

            # top bubble edge
            lines.append((side, [(" " + "▄" * inner + " ", A(bp))]))
            for row in wrapped_rows:
                segs = []
                used = 0
                for t, a, st in row:
                    if st == "codeblock":
                        # left pad indicator
                        segs.append((" │" + t, a))
                        used += len(t) + 2
                    else:
                        segs.append((t, a))
                        used += len(t)
                pad = " " * max(0, inner - used)
                # left/right padding inside bubble
                full = [(" ", ba)] + segs + [(pad + " ", ba)]
                lines.append((side, full))
            # bottom bubble edge
            lines.append((side, [(" " + "▀" * inner + " ", A(bp))]))
        return lines

    def draw_pane(self, h, w, sw):
        c = self.cur
        px, pw = sw, w - sw
        if c is None:
            cx = px + max(0, (pw - 28) // 2)
            self.safe(h // 2 - 2, cx, "✦  Nyx  ✦", A(3) | self.curses.A_BOLD)
            self.safe(h // 2,     cx - 2, "Private messenger over Tor", A(7))
            self.safe(h // 2 + 2, cx - 4, "Ctrl+N  contact   Ctrl+G  group", A(7))
            self.safe(h // 2 + 3, cx - 4, "Ctrl+Y  channel  Ctrl+U  @user", A(7))
            self.safe(h // 2 + 4, cx - 4, "**bold**  *italic*  `code`", A(7))
            return
        if c.get("is_group"):
            badge = "CH" if c.get("type") == "channel" else "GR"
            self.safe(1, px + 2, badge.center(4),
                      A(40 + c.get("k", 0)) | self.curses.A_BOLD)
            self.safe(2, px + 2, "    ", A(40 + c.get("k", 0)))
            prefix = "# " if c.get("type") == "channel" else ""
            self.safe(1, px + 7, prefix + visual(c["name"]), A(1) | self.curses.A_BOLD)
            nmem = len(c.get("members") or [])
            status = f"{nmem} members" if c.get("type") == "group" else f"channel · {nmem} subs"
            self.safe(2, px + 7, status, A(7))
        else:
            self.safe(1, px + 2, c["name"][:2].upper().center(4),
                      A(40 + c.get("k", 0)) | self.curses.A_BOLD)
            self.safe(2, px + 2, "    ", A(40 + c.get("k", 0)))
            self.safe(1, px + 7, visual(c["name"]), A(1) | self.curses.A_BOLD)
            hot = c.get("last_t") and time.time() - c["last_t"] < 300
            status = "● online" if hot else "○ offline"
            self.safe(2, px + 7, status,
                      (A(3) | self.curses.A_BOLD) if hot else A(7))
        hint = "Ctrl+F file · Ctrl+D save · F2 theme · Esc"
        if pw > len(hint) + 16:
            self.safe(1, w - len(hint) - 2, hint, A(7))
        self.safe(3, px, "─" * pw, A(7))
        top, bot = 4, h - 5
        ah = bot - top + 1
        lines = self.build(c, pw)
        n = len(lines)
        self.scroll = max(0, min(self.scroll, max(0, n - ah)))
        start = n - ah - self.scroll
        off = max(0, 10 - (self.frame - self.open_t) * 2)
        for r in range(ah):
            i = start + r
            if i < 0 or i >= n:
                continue
            side, segs = lines[i]
            tot = sum(len(t) for t, _ in segs)
            x = (px + 2 if side == "L" else px + pw - 2 - tot) + off
            for t, a in segs:
                self.safe(top + r, x, t, a)
                x += len(t)
        if self.scroll:
            self.safe(bot, px + pw - 12, " ↓ newer ",
                      A(9) | self.curses.A_BOLD)
        # input box
        y0 = h - 3
        bw = pw - 4
        inner = bw - 4
        edge = (A(3) | self.curses.A_BOLD) if self.buf else A(7)
        self.safe(y0, px + 2, "╭" + "─" * (bw - 2) + "╮", edge)
        self.safe(y0 + 2, px + 2, "╰" + "─" * (bw - 2) + "╯", edge)
        if self.buf:
            cur = "▌" if (self.frame // 12) % 2 == 0 else " "
            vis = visual(self.buf)[-(inner - 4):]
            if _is_rtl(self.buf[-1]):
                body = "› " + cur + vis
            else:
                body = "› " + vis + cur
            ba = A(1)
        else:
            body, ba = "› Message…  **bold** *italic* `code`", A(7)
        self.safe(y0 + 1, px + 2, "│ ", edge)
        self.safe(y0 + 1, px + 4, body.ljust(inner), ba)
        self.safe(y0 + 1, px + 4 + inner, " │", edge)

    def draw_overlay(self, h, w):
        f = self.frame
        if self.toast:
            txt, t0, until = self.toast
            if f >= until:
                self.toast = None
            else:
                lab = " " + visual(txt) + " "
                x = w - len(lab) - 2 + max(0, 14 - (f - t0) * 3)
                self.safe(0, x, lab, A(9) | self.curses.A_BOLD)
        if self.curtain is not None:
            p = f - self.curtain
            if p < 8:
                x0, x1 = 0, int((p + 1) / 8 * w)
            else:
                x0, x1 = int((p - 8 + 1) / 8 * w), w
            for y in range(h):
                if x1 > x0:
                    self.safe(y, x0, " " * (x1 - x0), A(9))
                edge = x1 if p < 8 else x0 - 3
                if p < 8:
                    self.safe(y, edge, "▓▒░", A(9))
                else:
                    self.safe(y, max(0, edge), "░▒▓", A(9))

    # ---------- panels (subwindow-based, no flicker) ----------
    def _paint_panel(self, y0, x0, bw, bh, title=""):
        # shadow
        for y in range(y0 + 1, min(self.s.getmaxyx()[0], y0 + bh + 1)):
            self.safe(y, x0 + 2, " " * bw, A(18))
        # body
        for y in range(y0, min(self.s.getmaxyx()[0], y0 + bh)):
            self.safe(y, x0, " " * bw, A(15))
        # borders
        self.safe(y0, x0, "╭" + "─" * (bw - 2) + "╮", A(17) | self.curses.A_BOLD)
        self.safe(y0 + bh - 1, x0, "╰" + "─" * (bw - 2) + "╯",
                  A(17) | self.curses.A_BOLD)
        for y in range(y0 + 1, y0 + bh - 1):
            self.safe(y, x0, "│", A(17) | self.curses.A_BOLD)
            self.safe(y, x0 + bw - 1, "│", A(17) | self.curses.A_BOLD)
        if title:
            self.safe(y0, x0 + 3, title, A(17) | self.curses.A_BOLD)

    def modal_input(self, title, prompt, default="", allow_empty=False):
        curses = self.curses
        buf = default
        self.draw()
        h, w = self.s.getmaxyx()

        bw = min(66, w - 6)
        bh = 11
        if h < bh + 3 or w < 30:
            return None
        y0 = max(1, (h - bh) // 2)
        x0 = max(2, (w - bw) // 2)

        win = curses.newwin(bh, bw, y0, x0)
        win.keypad(True)
        win.timeout(80)

        def wput(y, x, text, attr=0):
            if y < 0 or y >= bh or x < 0 or x >= bw or not text:
                return
            text = text[: max(0, bw - x - 1)]  # never touch last column
            try:
                win.addstr(y, x, text, attr)
            except curses.error:
                pass

        # static paint
        for y in range(bh):
            wput(y, 0, " " * (bw - 1), A(15))

        wput(0, 0, "╭" + "─" * (bw - 3), A(17) | curses.A_BOLD)
        wput(0, bw - 2, "╮", A(17) | curses.A_BOLD)
        wput(bh - 1, 0, "╰" + "─" * (bw - 3), A(17) | curses.A_BOLD)
        wput(bh - 1, bw - 2, "╯", A(17) | curses.A_BOLD)
        for y in range(1, bh - 1):
            wput(y, 0, "│", A(17) | curses.A_BOLD)
            wput(y, bw - 2, "│", A(17) | curses.A_BOLD)
        wput(0, 3, "  " + title + "  ", A(17) | curses.A_BOLD)
        wput(2, 4, prompt, A(16))

        iw = bw - 8
        wput(4, 4, "╭" + "─" * (iw - 2) + "╮", A(16))
        wput(5, 4, "│", A(16))
        wput(5, 4 + iw - 1, "│", A(16))
        wput(6, 4, "╰" + "─" * (iw - 2) + "╯", A(16))
        hint = "Enter  ·  Esc"
        wput(bh - 2, max(0, (bw - len(hint)) // 2), hint, A(16))
        win.refresh()

        row, col, tw = 5, 6, bw - 10
        while True:
            self.frame += 1
            cur = "▌" if (self.frame // 8) % 2 == 0 else " "
            v = visual(buf) if buf else ""
            if buf and _is_rtl(buf[-1]):
                line = "› " + cur + v
            else:
                line = "› " + v + cur
            line = line[:tw].ljust(tw)
            wput(row, col, line, A(19) | curses.A_BOLD)
            win.refresh()
            try:
                k = win.get_wch()
            except curses.error:
                continue
            if not isinstance(k, str):
                continue
            if k == "\x1b":
                del win
                return None
            if k in ("\n", "\r"):
                if buf or allow_empty:
                    del win
                    return buf.strip()
            elif k in ("\x7f", "\b"):
                buf = buf[:-1]
            elif k.isprintable():
                buf += k

    # ---------- input ----------
    def send(self):
        c = self.cur
        text = self.buf.strip()
        if not text or not c or not self.node:
            return
        self.buf = ""
        try:
            if c.get("is_group"):
                # channel: only owner may post
                if c.get("type") == "channel":
                    if (c.get("owner") or "").lower() != (self.pub or "").lower():
                        self.toast_msg("Only the channel owner can post")
                        return
                targets = list(c.get("members") or [])
                tags = [["g", c["id"]], ["subject", c.get("name", "")],
                        ["nyx", c.get("type", "group")]]
                rumor, _ = self.node.send_rumor(targets, 14, text, tags=tags)
            else:
                rumor, _ = self.node.send_rumor([c["pub"]], 14, text)
            self.sent_ids.add(rumor["id"])
            self.append_msg(c, self.pub, text, mine=True)
            c["last_t"] = time.time()
            c["order"] = time.time()
            self.refresh_chats()
        except Exception as e:
            self.toast_msg("send failed: " + str(e)[:40])

    def key(self, k):
        curses = self.curses
        if k is None:
            return False
        if k == curses.KEY_RESIZE:
            return False
        if isinstance(k, str):
            if k in ("\x1b", "\x03"):
                return True
            if k in ("\n", "\r"):
                self.send()
            elif k == "\x0e":          # Ctrl+N
                self.open_add_contact()
            elif k == "\x05":          # Ctrl+E
                self.set_name_dialog()
            elif k == "\x15":          # Ctrl+U
                self.set_username_dialog()
            elif k == "\x07":          # Ctrl+G  new group
                self.create_group_dialog(as_channel=False)
            elif k == "\x19":          # Ctrl+Y  new channel
                self.create_group_dialog(as_channel=True)
            elif k == "\x0b":          # Ctrl+K  add member to group
                self.add_group_member_dialog()
            elif k == "\x06":          # Ctrl+F
                self.send_file_dialog()
            elif k == "\x04":          # Ctrl+D
                self.download_last_file()
            elif k == "\x14":          # Ctrl+T
                self.start_theme()
            elif k in ("\x7f", "\b"):
                self.buf = self.buf[:-1]
            elif k.isprintable():
                self.buf += k
            return False
        if k == curses.KEY_UP:
            self.move(-1)
        elif k == curses.KEY_DOWN:
            self.move(1)
        elif k == getattr(curses, "KEY_F2", -1):
            self.start_theme()
        elif k == curses.KEY_PPAGE:
            self.scroll += 5
        elif k == curses.KEY_NPAGE:
            self.scroll = max(0, self.scroll - 5)
        elif k == curses.KEY_BACKSPACE:
            self.buf = self.buf[:-1]
        elif k == getattr(curses, "KEY_ENTER", -1):
            self.send()
        return False

    def start_theme(self):
        if self.curtain is None:
            self.curtain = self.frame

    # ---------- splash ----------
    def splash(self):
        curses = self.curses
        s = self.s
        s.timeout(30)
        h, w = s.getmaxyx()
        stars = [(secrets.randbelow(max(1, h)), secrets.randbelow(max(1, w)),
                  secrets.randbelow(18))
                 for _ in range(max(1, h * w // 35))]
        tag = "private  ·  fast  ·  yours"
        for f in range(50):
            s.erase()
            h, w = s.getmaxyx()
            for y, x, p in stars:
                if y < h and x < w:
                    st = (f + p) // 5 % 3
                    self.safe(y, x, ".+*"[st],
                              A(7) if st == 0 else
                              (A(3) | (curses.A_BOLD if st == 2 else 0)))
            ly = max(0, h // 2 - 4)
            lw = max(len(l) for l in LOGO)
            lx = max(0, (w - lw) // 2)
            reveal = f * 2
            for r, line in enumerate(LOGO):
                for c0 in range(0, min(reveal, len(line)), 2):
                    idx = ((c0 // 2) - f // 2) % 6
                    self.safe(ly + r, lx + c0, line[c0:c0 + 2],
                              A(50 + idx) | curses.A_BOLD)
            if f > 20:
                n = f - 20
                self.safe(ly + 7, max(0, (w - len(tag)) // 2), tag[:n], A(1))
            s.refresh()
            if s.getch() != -1:
                break
        s.timeout(60)

    def run(self):
        self.splash()
        if not self.load_or_create_account():
            return
        self.load_profile()
        self.load_groups()
        self.onboarding()
        self.start_node()
        while True:
            self.frame += 1
            self.tick()
            self.draw()
            try:
                k = self.s.get_wch()
            except self.curses.error:
                k = None
            if self.key(k):
                break
        if self.node:
            self.node.stop()


def main(scr):
    import curses
    if curses.COLORS < 256:
        raise SystemExit("nyx needs a 256-color terminal (try TERM=xterm-256color).")
    if getattr(curses, "COLOR_PAIRS", 0) < 60:
        raise SystemExit("nyx needs a terminal with at least 60 color pairs.")
    try:
        App(scr).run()
    except KeyboardInterrupt:
        pass


def entry():
    if "--version" in sys.argv:
        from nyxchat import __version__
        print(f"nyx {__version__}")
        return
    bootstrap.ensure_all()
    os.environ["PATH"] = str(bootstrap.BIN) + os.pathsep + os.environ.get("PATH", "")
    _load()
    import curses
    curses.wrapper(main)


if __name__ == "__main__":
    entry()
