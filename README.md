# Nyx

Private terminal messenger on Nostr over Tor.

[![PyPI](https://img.shields.io/pypi/v/nyxchat.svg)](https://pypi.org/project/nyxchat/)
[![Python](https://img.shields.io/pypi/pyversions/nyxchat.svg)](https://pypi.org/project/nyxchat/)
[![License](https://img.shields.io/pypi/l/nyxchat.svg)](https://pypi.org/project/nyxchat/)

## About

Nyx is a private, decentralized terminal messenger built on the Nostr protocol. All traffic is routed through Tor, making it resistant to censorship and surveillance. It features end-to-end encrypted messaging, file transfer, and full Persian/Arabic text support.

## Features

- **Nostr protocol** - Decentralized social protocol with no central servers
- **Tor integration** - All traffic routed through Tor for anonymity
- **End-to-end encryption** - NIP-44 v2 encrypted direct messages (NIP-17 gift wrap)
- **File transfer** - Send and receive files over Blossom protocol with AES-GCM encryption
- **Persian/Arabic support** - Full BiDi text rendering and Arabic letter shaping
- **Multiple themes** - 6 built-in color themes (Midnight, Aurora, Sakura, Matrix, Sunset, Forest)
- **Bridge support** - Auto-fetch obfs4 bridges for censored regions
- **Pure Python** - No native dependencies, works on Windows/Linux/macOS
- **Lightweight** - Minimal resource usage, runs in any terminal

## Install

```bash
pip install nyxchat
```

## Run

```bash
nyx
```

First run auto-installs a local Tor and any missing Python deps.

## Keys

| Key | Action |
|-----|--------|
| Up/Down | switch chat |
| Enter | send message (or save last file in chat) |
| Ctrl+N | add contact (npub / hex / @alias / user@domain) |
| Ctrl+E | your display name |
| Ctrl+U | your NIP-05 username |
| Ctrl+F | send a file |
| Ctrl+D | download last file in current chat |
| F2 | change theme |
| PgUp/PgDn | scroll |
| Esc/Ctrl+C | quit |

## How It Works

1. **Identity** - Generates or imports a Nostr keypair (nsec/npub)
2. **Tor** - Launches a private Tor process with obfs4 bridges for censorship resistance
3. **Relays** - Connects to Nostr relays over Tor via WebSocket
4. **Messaging** - Sends and receives NIP-17 gift-wrapped, NIP-44 encrypted messages
5. **Files** - Encrypts files with AES-GCM, uploads to Blossom servers over Tor

## Config

```
~/.nyx/key            your nsec  (chmod 600)
~/.nyx/profile.json   display name, username
~/.nyx/contacts.json  contact list
~/.nyx/tor/           private Tor datadir
~/.nyx/bin/tor        private Tor binary
```

Override with env `NYX_HOME=/some/path`.

## Topics

`nostr` `tor` `messenger` `privacy` `encryption` `terminal` `tui` `chat` `decentralized` `censorship-resistance` `obfs4` `bridges` `persian` `arabic` `bidi`

## License

MIT
