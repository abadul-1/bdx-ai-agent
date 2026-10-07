# BDX AI

**Ethical Cybersecurity AI for Termux.**

BDX AI is a lightweight local assistant that runs a web server on
`127.0.0.1` and serves a futuristic chat interface. It talks to
[OpenRouter](https://openrouter.ai) using an API key that you supply via an
environment variable. The key is never written to disk and never exposed to
the browser.

---

## Features

- Local-only server (`127.0.0.1`) — not exposed to your network
- Modern, mobile-first, dark/neon UI
- Streaming-free, low-RAM friendly (works on ARM32)
- Uses `openrouter/free` by default — swap models with `OPENROUTER_MODEL`
- Refuses harmful requests and stays focused on **ethical** cybersecurity
- Zero tracking, zero logging of conversation content

---

## Installation

Add the BDX APT repository (one time), then install:

```bash
pkg update
pkg install bdxai
```

If you are building locally from source:

```bash
git clone https://github.com/<you>/bdxai.git
cd bdxai
bash build.sh
dpkg -i build/bdxai_1.0.0_all.deb
```

---

## Setup: OpenRouter API key

1. Get a free key at <https://openrouter.ai/keys>
2. Export it in Termux (add to `~/.bashrc` to persist):

```bash
export OPENROUTER_API_KEY='sk-or-v1-...'
```

Run:

```bash
bdxai
```

If the key is missing, `bdxai` prints a friendly setup message and exits.

---

## Running

```bash
bdxai
```

This starts the server and opens `http://127.0.0.1:8080` in a browser if
`termux-open-url` (from `termux-api`) is available. Otherwise, open the URL
manually.

Stop with `Ctrl+C`.

---

## Changing the port

```bash
export BDXAI_PORT=9090
bdxai
```

The server always binds to `127.0.0.1`, never `0.0.0.0`.

---

## Changing the model

```bash
export OPENROUTER_MODEL='openai/gpt-4o-mini'
bdxai
```

Any model slug that OpenRouter supports works. Default: `openrouter/free`.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Set your OpenRouter API key` | `export OPENROUTER_API_KEY='...'` and rerun |
| `Invalid OpenRouter API key (401)` | Check the key on openrouter.ai |
| `Rate limit reached` | Wait a moment or switch models |
| `Port 8080 is already in use` | `export BDXAI_PORT=8081` |
| `Network error` | Check your connection; try again |
| Blank page | Hard-refresh the browser, or visit `/health` |
| `Python not found` | `pkg install python` |

---

## Security notes

- Binds to `127.0.0.1` only. **Never** change this to `0.0.0.0` unless you
  understand the risks and put a firewall/auth in front of it.
- Your `OPENROUTER_API_KEY` lives only in your shell environment. The browser
  never sees it. The server holds it in memory for the duration of a request.
- No conversation data is written to disk.
- No telemetry, no analytics, no tracking.
- The assistant is instructed to refuse unauthorized/harmful requests.

---

## License

MIT