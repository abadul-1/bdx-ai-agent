#!/data/data/com.termux/files/usr/bin/bash
# BDX AI - Termux package build script
# Produces a .deb package named bdxai_1.0.0_all.deb

set -e

PKG_NAME="bdxai"
PKG_VERSION="1.0.0"
PKG_ARCH="all"
PKG_MAINTAINER="BDX"
PKG_DESCRIPTION="BDX AI ethical cybersecurity assistant for Termux."
PKG_LICENSE="MIT"

WORKDIR="$(pwd)"
BUILD_DIR="$WORKDIR/build"
PKG_ROOT="$BUILD_DIR/${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}"

echo "[*] Cleaning previous build..."
rm -rf "$BUILD_DIR"
mkdir -p "$PKG_ROOT/DEBIAN"
mkdir -p "$PKG_ROOT/data/data/com.termux/files/usr/bin"
mkdir -p "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/static"

echo "[*] Copying files..."
cp -f "$WORKDIR/files/bin/bdxai" \
      "$PKG_ROOT/data/data/com.termux/files/usr/bin/bdxai"
cp -f "$WORKDIR/files/share/bdxai/server.py" \
      "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/server.py"
cp -f "$WORKDIR/files/share/bdxai/app.py" \
      "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/app.py"
cp -f "$WORKDIR/files/share/bdxai/static/"* \
      "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/static/"

chmod 755 "$PKG_ROOT/data/data/com.termux/files/usr/bin/bdxai"
chmod 644 "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/server.py"
chmod 644 "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/app.py"
chmod 644 "$PKG_ROOT/data/data/com.termux/files/usr/share/bdxai/static/"*

echo "[*] Writing DEBIAN/control..."
cat > "$PKG_ROOT/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${PKG_VERSION}
Architecture: ${PKG_ARCH}
Maintainer: ${PKG_MAINTAINER}
Depends: python
Section: utils
Priority: optional
Homepage: https://github.com/bdx/bdxai
License: ${PKG_LICENSE}
Description: ${PKG_DESCRIPTION}
 BDX AI runs a local-only web server (127.0.0.1) that serves a
 futuristic ethical cybersecurity chat interface powered by
 OpenRouter. It never exposes your API key to the browser and
 binds only to localhost by default.
EOF

echo "[*] Writing postinst..."
cat > "$PKG_ROOT/DEBIAN/postinst" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
chmod 755 /data/data/com.termux/files/usr/bin/bdxai 2>/dev/null || true
exit 0
EOF
chmod 755 "$PKG_ROOT/DEBIAN/postinst"

echo "[*] Writing prerm..."
cat > "$PKG_ROOT/DEBIAN/prerm" <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
exit 0
EOF
chmod 755 "$PKG_ROOT/DEBIAN/prerm"

echo "[*] Building .deb..."
cd "$BUILD_DIR"
dpkg-deb --build --root-owner-group "${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}"

echo "[*] Done: $BUILD_DIR/${PKG_NAME}_${PKG_VERSION}_${PKG_ARCH}.deb"