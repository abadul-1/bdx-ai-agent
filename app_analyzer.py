#!/usr/bin/env python3
"""
app_analyzer.py
----------------
Safe, local-only Android APK static analyzer for BD-X AI.

What it does:
- Extracts the APK (zip).
- Reads every file as bytes, runs regex over readable strings.
- Finds: URLs, API paths, websocket endpoints, IPv4 literals,
  and secret-looking tokens (flagged, NOT used).
- Optional: if `apktool` is available, decode resources + smali first
  for better coverage.
- Writes a JSON report.

What it does NOT do:
- No network requests.
- No authentication.
- No token/key usage.
- No target interaction whatsoever.
"""

import os
import re
import json
import shutil
import zipfile
import subprocess
import tempfile
from pathlib import Path
from collections import defaultdict
from datetime import datetime


# --------------------------------------------------------------------------
# Regexes
# --------------------------------------------------------------------------

RE_URL       = re.compile(rb"https?://[a-zA-Z0-9\.\-_:/?#\[\]@!$&'()*+,;=%]{4,200}")
RE_WS        = re.compile(rb"wss?://[a-zA-Z0-9\.\-_:/?#\[\]@!$&'()*+,;=%]{4,200}")
RE_API_PATH  = re.compile(rb"/(?:api|v\d+|rest|graphql|gql)(?:/[a-zA-Z0-9_\-\{\}\.]+){0,6}")
RE_IPV4      = re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")

SECRET_PATTERNS = {
    "google_api_key":  re.compile(rb"AIza[0-9A-Za-z_\-]{35}"),
    "aws_access_key":  re.compile(rb"AKIA[0-9A-Z]{16}"),
    "bearer_token":    re.compile(rb"Bearer\s+[A-Za-z0-9\-\._~\+/]{20,}"),
    "jwt":             re.compile(rb"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    "firebase":        re.compile(rb"[a-z0-9\-]+\.firebaseio\.com"),
    "supabase":        re.compile(rb"[a-z0-9]+\.supabase\.co"),
    "generic_apikey":  re.compile(rb"(?i)(?:api[_\-]?key|apikey|access[_\-]?token|client[_\-]?secret)\s*[:=]\s*[\"']?([A-Za-z0-9\-_\.]{16,})[\"']?"),
}

# Things we consider noise when collecting domains
DOMAIN_WHITELIST = {
    b"schemas.android.com",
    b"www.w3.org",
    b"example.com",
    b"localhost",
    b"127.0.0.1",
    b"0.0.0.0",
    b"github.com",
    b"google.com",
    b"googleapis.com",
    b"gstatic.com",
    b"android.com",
}


# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------

def _run(cmd, cwd=None, timeout=300):
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def has_tool(name):
    return shutil.which(name) is not None


def _safe_text(b: bytes) -> str:
    try:
        return b.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def _is_noise_domain(domain_bytes: bytes) -> bool:
    d = domain_bytes.lower()
    for w in DOMAIN_WHITELIST:
        if w in d:
            return True
    # drop obvious java/kotlin package paths disguised as urls
    if d.startswith(b"http://schemas.") or d.startswith(b"http://www.w3."):
        return True
    return False


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_apk(apk_path: Path, workdir: Path) -> Path:
    """Unzip APK into workdir/apk_unzipped"""
    out = workdir / "apk_unzipped"
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk_path, "r") as z:
        z.extractall(out)
    return out


def try_apktool(apk_path: Path, workdir: Path) -> Path | None:
    """If apktool is installed, decode smali+resources."""
    if not has_tool("apktool"):
        return None
    out = workdir / "apktool_out"
    rc, so, se = _run(
        ["apktool", "d", "-f", "-o", str(out), str(apk_path)],
        timeout=600,
    )
    if rc == 0 and out.exists():
        return out
    return None


def collect_files(root: Path, max_size=8 * 1024 * 1024):
    """Yield (path, bytes) for reasonable-size files."""
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size == 0 or size > max_size:
            continue
        try:
            yield p, p.read_bytes()
        except OSError:
            continue


def find_strings_in_binary(data: bytes, min_len=5):
    """Rough `strings`-like extraction for compiled .so/.dex binaries."""
    out = []
    cur = bytearray()
    for b in data:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                out.append(bytes(cur))
            cur = bytearray()
    if len(cur) >= min_len:
        out.append(bytes(cur))
    return out


# --------------------------------------------------------------------------
# Analyzer
# --------------------------------------------------------------------------

def analyze_apk(apk_path: str | Path, out_json: str | Path | None = None) -> dict:
    apk_path = Path(apk_path).expanduser().resolve()
    if not apk_path.exists():
        raise FileNotFoundError(f"APK not found: {apk_path}")
    if apk_path.suffix.lower() != ".apk":
        raise ValueError("Expected a .apk file")

    workdir = Path(tempfile.mkdtemp(prefix="bdx_apk_"))
    report = {
        "apk": str(apk_path),
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "apktool_used": False,
        "urls": [],
        "websockets": [],
        "api_paths": [],
        "ipv4": [],
        "secrets_flagged": defaultdict(list),
        "domains": [],
        "notes": [],
    }

    urls       = set()
    wss        = set()
    api_paths  = set()
    ipv4s      = set()
    domains    = set()
    secrets    = defaultdict(set)

    # 1. extract raw
    extract_root = extract_apk(apk_path, workdir)

    # 2. optional apktool decode for more coverage
    apktool_root = try_apktool(apk_path, workdir)
    scan_roots = [extract_root]
    if apktool_root:
        scan_roots.append(apktool_root)
        report["apktool_used"] = True
    else:
        report["notes"].append(
            "apktool not installed; scanned raw APK contents only "
            "(some resources may remain encoded)."
        )

    # 3. walk & scan
    for root in scan_roots:
        for path, data in collect_files(root):
            chunks = [data]
            # for binaries, also pull printable strings
            if path.suffix.lower() in (".dex", ".so", ".bin", ""):
                chunks.extend(find_strings_in_binary(data))

            for chunk in chunks:
                for m in RE_URL.finditer(chunk):
                    u = m.group(0)
                    if not _is_noise_domain(u):
                        urls.add(u)
                for m in RE_WS.finditer(chunk):
                    wss.add(m.group(0))
                for m in RE_API_PATH.finditer(chunk):
                    p = m.group(0)
                    if len(p) >= 5:
                        api_paths.add(p)
                for m in RE_IPV4.finditer(chunk):
                    ip = m.group(0)
                    # filter obvious non-IP matches
                    parts = ip.split(b".")
                    if all(0 <= int(x) <= 255 for x in parts):
                        if ip not in (b"0.0.0.0", b"127.0.0.1"):
                            ipv4s.add(ip)
                for name, pat in SECRET_PATTERNS.items():
                    for m in pat.finditer(chunk):
                        val = m.group(0)
                        # redact middle of the secret
                        sval = val.decode("utf-8", errors="ignore")
                        if len(sval) > 12:
                            sval = sval[:6] + "…REDACTED…" + sval[-4:]
                        secrets[name].add(sval)

    # 4. domain aggregation from URLs
    for u in urls:
        try:
            from urllib.parse import urlparse
            host = urlparse(u.decode("utf-8", "ignore")).hostname
            if host:
                domains.add(host.encode())
        except Exception:
            pass

    # 5. clean non-printable / dedupe
    def clean(v):
        return sorted({x.decode("utf-8", "ignore") for x in v if x})

    report["urls"]            = clean(urls)
    report["websockets"]      = clean(wss)
    report["api_paths"]       = clean(api_paths)
    report["ipv4"]            = clean(ipv4s)
    report["domains"]         = clean(domains)
    report["secrets_flagged"] = {k: sorted(v) for k, v in secrets.items()}

    # 6. write report
    if out_json is None:
        out_json = apk_path.with_suffix(".bdx_report.json")
    Path(out_json).write_text(
        json.dumps(report, indent=2, ensure_ascii=False)
    )
    report["_report_path"] = str(out_json)

    # cleanup tmp
    shutil.rmtree(workdir, ignore_errors=True)
    return report


# --------------------------------------------------------------------------
# Pretty printer for the agent
# --------------------------------------------------------------------------

def render_report(r: dict) -> str:
    lines = []
    lines.append(f"# BD-X APK Analysis Report")
    lines.append("")
    lines.append(f"- **APK**: `{r['apk']}`")
    lines.append(f"- **Scanned at**: {r['scanned_at']}")
    lines.append(f"- **apktool used**: {r['apktool_used']}")
    lines.append(f"- **Report JSON**: `{r.get('_report_path','')}`")
    lines.append("")

    def section(title, items, limit=80):
        lines.append(f"## {title}  ({len(items)})")
        if not items:
            lines.append("_none_")
        else:
            for x in items[:limit]:
                lines.append(f"- `{x}`")
            if len(items) > limit:
                lines.append(f"- … +{len(items)-limit} more (see JSON)")
        lines.append("")

    section("Domains", r["domains"])
    section("URLs", r["urls"])
    section("WebSockets", r["websockets"])
    section("API paths", r["api_paths"], limit=120)
    section("IPv4 literals", r["ipv4"])

    lines.append("## Secret-like strings (flagged, REDACTED)")
    if not r["secrets_flagged"]:
        lines.append("_none_")
    else:
        for k, v in r["secrets_flagged"].items():
            lines.append(f"### {k}  ({len(v)})")
            for x in v[:20]:
                lines.append(f"- `{x}`")
            if len(v) > 20:
                lines.append(f"- … +{len(v)-20} more")
    lines.append("")
    lines.append("> ⚠️ Secrets are **redacted** and **never used**. If any belong to you, rotate them.")
    return "\n".join(lines)