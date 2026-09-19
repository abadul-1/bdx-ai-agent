#!/usr/bin/env python3
"""
ssl_pinner.py
-------------
Local-only SSL pinning patcher for APKs you own / are authorized to test.

What it does:
  - decompiles APK with apktool
  - neutralizes common SSL pinning implementations:
      * network_security_config.xml <pin-set> removed
      * user-installed CAs trusted via <base-config>
      * OkHttp CertificatePinner.check -> return-void
      * X509TrustManager.checkServerTrusted -> no-op
      * HostnameVerifier.verify -> return true
  - rebuilds the APK (apktool b)
  - signs with a debug keystore so it installs
  - never overwrites the original file (writes *_patched.apk)

What it does NOT do:
  - no network requests
  - no credential capture
  - no tampering with apps you don't own / aren't authorized to test
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _run(cmd, cwd=None, timeout=900):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _has(name):
    return shutil.which(name) is not None


def _read(p: Path) -> str:
    return p.read_text(errors="ignore")


def _write(p: Path, text: str):
    p.write_text(text)


# --------------------------------------------------------------------------
# Patch 1: network_security_config.xml
# --------------------------------------------------------------------------

NSC_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system" />
            <certificates src="user" />
        </trust-anchors>
    </base-config>
</network-security-config>
"""


def patch_nsc(decoded: Path) -> int:
    """Neutralize pin sets by rewriting all network_security_config.xml files."""
    count = 0
    for nsc in decoded.rglob("network_security_config*.xml"):
        try:
            _write(nsc, NSC_TEMPLATE)
            count += 1
        except OSError:
            pass
    # Also look for files with pin-set inside res/xml
    for xml in decoded.rglob("res/xml/*.xml"):
        try:
            txt = _read(xml)
        except OSError:
            continue
        if "<pin-set" in txt or "<pin digest" in txt:
            _write(xml, NSC_TEMPLATE)
            count += 1
    return count


def patch_manifest_nsc(decoded: Path) -> bool:
    """Force android:networkSecurityConfig and usesCleartextTraffic in manifest."""
    manifest = decoded / "AndroidManifest.xml"
    if not manifest.exists():
        return False
    try:
        txt = _read(manifest)
    except OSError:
        return False

    changed = False

    # ensure nsc attribute points to our file
    if "android:networkSecurityConfig=" not in txt:
        txt = txt.replace(
            "<application",
            '<application android:networkSecurityConfig="@xml/network_security_config"',
            1,
        )
        changed = True

    # allow cleartext
    if 'android:usesCleartextTraffic=' in txt:
        txt = re.sub(
            r'android:usesCleartextTraffic="[^"]*"',
            'android:usesCleartextTraffic="true"',
            txt,
        )
        changed = True
    else:
        txt = txt.replace(
            "<application",
            '<application android:usesCleartextTraffic="true"',
            1,
        )
        changed = True

    if changed:
        _write(manifest, txt)
    return changed


# --------------------------------------------------------------------------
# Patch 2: smali patterns
# --------------------------------------------------------------------------

# OkHttp CertificatePinner.check -> return-void
OKHTTP_PINNER_PATTERNS = [
    # L okhttp3/CertificatePinner;->check(Ljava/lang/String;Ljava/util/List;)V
    (
        re.compile(
            r"(\.method\s+(?:public\s+|private\s+)?(?:final\s+)?check\("
            r"Ljava/lang/String;\s*Ljava/util/List;\)V)"
        ),
        r"\1\n    .locals 0\n    return-void",
    ),
]

# Generic checkServerTrusted -> return-void
TRUSTMANAGER_PATTERN = re.compile(
    r"(\.method\s+(?:public\s+)?checkServerTrusted\("
    r"\[Ljava/security/cert/X509Certificate;\s*Ljava/lang/String;\)V)"
)

# HostnameVerifier.verify -> return true
HOSTNAME_VERIFIER_PATTERN = re.compile(
    r"(\.method\s+(?:public\s+)?verify\("
    r"Ljava/lang/String;\s*Ljavax/net/ssl/SSLSession;\)Z)"
)


def patch_smali(decoded: Path) -> dict:
    """Walk all .smali files and neutralize common pinning patterns."""
    stats = {
        "okhttp_pinner": 0,
        "trustmanager": 0,
        "hostname_verifier": 0,
        "files_changed": 0,
    }

    for smali in decoded.rglob("*.smali"):
        try:
            txt = _read(smali)
        except OSError:
            continue
        orig = txt

        # 1. OkHttp CertificatePinner.check
        for pat, repl in OKHTTP_PINNER_PATTERNS:
            new_txt, n = pat.subn(repl, txt)
            if n:
                stats["okhttp_pinner"] += n
                txt = new_txt

        # 2. checkServerTrusted
        if TRUSTMANAGER_PATTERN.search(txt):
            def _tm(m):
                return m.group(1) + "\n    .locals 0\n    return-void"
            txt, n = TRUSTMANAGER_PATTERN.subn(_tm, txt)
            stats["trustmanager"] += n

        # 3. verify(String, SSLSession) -> true
        if HOSTNAME_VERIFIER_PATTERN.search(txt):
            def _hv(m):
                return (
                    m.group(1)
                    + "\n    .locals 1\n"
                    + "    const/4 v0, 0x1\n"
                    + "    return v0"
                )
            txt, n = HOSTNAME_VERIFIER_PATTERN.subn(_hv, txt)
            stats["hostname_verifier"] += n

        if txt != orig:
            try:
                _write(smali, txt)
                stats["files_changed"] += 1
            except OSError:
                pass

    return stats


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------

def _ensure_debug_keystore(path: Path) -> bool:
    """Create a debug keystore if not present."""
    if path.exists():
        return True
    if not _has("keytool"):
        return False
    rc, _, _ = _run([
        "keytool", "-genkeypair",
        "-keystore", str(path),
        "-alias", "bdx",
        "-storepass", "android",
        "-keypass", "android",
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=BD-X Debug,O=BDX,C=BD",
    ])
    return rc == 0


def sign_apk(unsigned_apk: Path, out_apk: Path) -> bool:
    """Sign with apksigner using a debug keystore."""
    keystore = Path.home() / ".bdx_debug.keystore"

    if not _has("apksigner"):
        return False
    if not _ensure_debug_keystore(keystore):
        return False

    rc, _, _ = _run([
        "apksigner", "sign",
        "--ks", str(keystore),
        "--ks-pass", "pass:android",
        "--key-pass", "pass:android",
        "--ks-key-alias", "bdx",
        "--out", str(out_apk),
        str(unsigned_apk),
    ])
    return rc == 0 and out_apk.exists()


def zipalign_apk(in_apk: Path, out_apk: Path) -> bool:
    if not _has("zipalign"):
        return False
    rc, _, _ = _run(["zipalign", "-f", "4", str(in_apk), str(out_apk)])
    return rc == 0 and out_apk.exists()


# --------------------------------------------------------------------------
# Main entry
# --------------------------------------------------------------------------

def patch_ssl_pinning(apk_path, out_dir=None, keep_decoded=False) -> dict:
    """
    Patch an APK's SSL pinning. Returns a report dict.

    Safety:
      - Original APK is never modified.
      - Output written as <name>_patched.apk
    """
    apk_path = Path(apk_path).expanduser().resolve()
    if not apk_path.exists():
        raise FileNotFoundError(f"APK not found: {apk_path}")
    if apk_path.suffix.lower() != ".apk":
        raise ValueError("Expected a .apk file")

    if not _has("apktool"):
        raise RuntimeError(
            "apktool not installed. Install it first "
            "(see earlier setup instructions)."
        )

    report = {
        "apk": str(apk_path),
        "output_apk": None,
        "patched": {
            "nsc_files_rewritten": 0,
            "manifest_updated": False,
            "smali": {
                "okhttp_pinner": 0,
                "trustmanager": 0,
                "hostname_verifier": 0,
                "files_changed": 0,
            },
        },
        "signed": False,
        "aligned": False,
        "notes": [],
    }

    base = out_dir or apk_path.parent
    base = Path(base)
    stem = apk_path.stem

    workdir = Path(tempfile.mkdtemp(prefix="bdx_sslpin_"))
    decoded = workdir / "decoded"
    rebuilt = workdir / f"{stem}_patched_unsigned.apk"

    # 1. decode
    rc, _, err = _run(
        ["apktool", "d", "-f", "-o", str(decoded), str(apk_path)],
        timeout=900,
    )
    if rc != 0 or not decoded.exists():
        report["notes"].append(f"apktool decode failed: {err[:200]}")
        return report

    # 2. patch NSC
    n = patch_nsc(decoded)
    report["patched"]["nsc_files_rewritten"] = n

    # ensure res/xml directory exists and nsc file present
    xml_dir = decoded / "res" / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    (xml_dir / "network_security_config.xml").write_text(NSC_TEMPLATE)

    # 3. patch manifest
    report["patched"]["manifest_updated"] = patch_manifest_nsc(decoded)

    # 4. patch smali
    stats = patch_smali(decoded)
    report["patched"]["smali"] = stats

    if (
        stats["okhttp_pinner"] == 0
        and stats["trustmanager"] == 0
        and stats["hostname_verifier"] == 0
        and not report["patched"]["manifest_updated"]
        and n == 0
    ):
        report["notes"].append(
            "No known pinning patterns matched. "
            "App may use a custom or packed implementation."
        )

    # 5. rebuild
    rc, _, err = _run(
        ["apktool", "b", "-o", str(rebuilt), str(decoded)],
        timeout=900,
    )
    if rc != 0 or not rebuilt.exists():
        report["notes"].append(f"apktool build failed: {err[:200]}")
        if not keep_decoded:
            shutil.rmtree(workdir, ignore_errors=True)
        return report

    # 6. zipalign (optional)
    aligned = workdir / f"{stem}_patched_aligned.apk"
    if zipalign_apk(rebuilt, aligned):
        report["aligned"] = True
        to_sign = aligned
    else:
        report["notes"].append("zipalign not available — skipping align.")
        to_sign = rebuilt

    # 7. sign
    final_apk = base / f"{stem}_patched.apk"
    if sign_apk(to_sign, final_apk):
        report["signed"] = True
        report["output_apk"] = str(final_apk)
    else:
        # fall back to unsigned
        shutil.copy2(to_sign, final_apk)
        report["output_apk"] = str(final_apk)
        report["notes"].append(
            "apksigner not available or signing failed — "
            "output is UNSIGNED (won't install)."
        )

    if not keep_decoded:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        report["decoded_dir"] = str(decoded)

    return report


def render_sslpin_report(r: dict) -> str:
    L = ["# BD-X SSL Pinning Patch Report", ""]
    L.append(f"- **Input APK**: `{r['apk']}`")
    L.append(f"- **Output APK**: `{r.get('output_apk')}`")
    L.append(f"- **Signed**: **{r['signed']}**")
    L.append(f"- **Aligned**: **{r['aligned']}**")
    L.append("")
    L.append("## Patches applied")
    L.append(f"- network_security_config.xml rewritten: **{r['patched']['nsc_files_rewritten']}**")
    L.append(f"- AndroidManifest updated: **{r['patched']['manifest_updated']}**")
    s = r["patched"]["smali"]
    L.append(f"- OkHttp CertificatePinner.check patched: **{s['okhttp_pinner']}**")
    L.append(f"- checkServerTrusted patched: **{s['trustmanager']}**")
    L.append(f"- HostnameVerifier.verify patched: **{s['hostname_verifier']}**")
    L.append(f"- smali files changed: **{s['files_changed']}**")
    if r.get("notes"):
        L.append("")
        L.append("## Notes")
        for n in r["notes"]:
            L.append(f"- {n}")
    L.append("")
    L.append("> ⚠️ Use ONLY on apps you own or are authorized to test.")
    L.append("> Re-signing changes the signature — the app must be uninstalled first.")
    return "\n".join(L)