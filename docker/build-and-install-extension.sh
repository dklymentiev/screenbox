#!/bin/bash
# Build CRX from extension/ and install via Chromium External Extensions.
# Run as root AFTER Chromium is installed, BEFORE switching to screenbox user.
#
# Builds CRX3 format using openssl (no browser launch needed).
set -euo pipefail

EXT_DIR="/opt/screenbox/extension"
PEM_FILE="/opt/screenbox/extension.pem"
CRX_FILE="/opt/screenbox/screenbox-extension.crx"

# 1. Generate PEM key
openssl genrsa 2048 > "$PEM_FILE" 2>/dev/null

# 2. Patch wsPort in background.js BEFORE building CRX
WS_PORT=${WS_BRIDGE_PORT:-8765}
sed -i "s/let wsPort = 8765;/let wsPort = ${WS_PORT};/" "$EXT_DIR/background.js"
echo "[build-ext] Patched background.js with wsPort=$WS_PORT"

# 3. Build CRX3 using openssl (no display needed)
ZIP_FILE="/tmp/extension.zip"
cd "$EXT_DIR" && zip -qr "$ZIP_FILE" . && cd /

# CRX3 format: magic + version + header_size + header + zip
# Header is a SignedData protobuf with the signature
DER_KEY=$(openssl rsa -in "$PEM_FILE" -pubout -outform DER 2>/dev/null)
SIGNATURE=$(openssl dgst -sha256 -sign "$PEM_FILE" < "$ZIP_FILE")

# Build CRX3 header (simplified: signed_header_data with sha256_with_rsa proof)
python3 - "$PEM_FILE" "$ZIP_FILE" "$CRX_FILE" <<'PYEOF'
import struct, subprocess, sys, os

pem_file, zip_file, crx_file = sys.argv[1], sys.argv[2], sys.argv[3]

with open(zip_file, "rb") as f:
    zip_data = f.read()

# Get DER public key
der_key = subprocess.check_output(
    ["openssl", "rsa", "-in", pem_file, "-pubout", "-outform", "DER"],
    stderr=subprocess.DEVNULL
)

# Sign the CRX3 signed data
# signed_header_data = "CRX3 SignedData\x00" + 4-byte LE len of signed_header + signed_header
# signed_header = just the crx_id (sha256 of public key, first 16 bytes)
import hashlib
crx_id = hashlib.sha256(der_key).digest()[:16]

# Build the signed_header protobuf (field 1 = crx_id, bytes)
# protobuf: field 1, wire type 2 (length-delimited) = tag 0x0a
signed_header = b'\x0a' + bytes([len(crx_id)]) + crx_id

# signed_header_data for signing
signed_header_size = struct.pack("<I", len(signed_header))
to_sign = b"CRX3 SignedData\x00" + signed_header_size + signed_header + zip_data

# RSA-SHA256 signature
import tempfile
with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
    tf.write(to_sign)
    tf_name = tf.name

signature = subprocess.check_output(
    ["openssl", "dgst", "-sha256", "-sign", pem_file, tf_name],
    stderr=subprocess.DEVNULL
)
os.unlink(tf_name)

# Build CRX3 header protobuf
# AsymmetricKeyProof: field 1 = public_key (bytes), field 2 = signature (bytes)
def encode_bytes_field(field_num, data):
    tag = (field_num << 3) | 2
    length = len(data)
    # varint encode length
    varint = b""
    while length > 0x7f:
        varint += bytes([(length & 0x7f) | 0x80])
        length >>= 7
    varint += bytes([length])
    return bytes([tag]) + varint + data

proof = encode_bytes_field(1, der_key) + encode_bytes_field(2, signature)

# CRXFileHeader: field 2 = signed_header (bytes), field 3 = sha256_with_rsa (repeated AsymmetricKeyProof)
header = encode_bytes_field(2, signed_header) + encode_bytes_field(3, proof)

# CRX3 file: magic "Cr24" + version(3) + header_size + header + zip
with open(crx_file, "wb") as f:
    f.write(b"Cr24")
    f.write(struct.pack("<I", 3))  # version
    f.write(struct.pack("<I", len(header)))
    f.write(header)
    f.write(zip_data)

print(f"[build-ext] CRX3 built: {crx_file} ({len(zip_data)} bytes zip, {len(header)} bytes header)")
PYEOF

if [ ! -f "$CRX_FILE" ]; then
    echo "[build-ext] ERROR: CRX build failed"
    exit 1
fi

rm -f "$ZIP_FILE"

# 4. Get extension ID from PEM
EXTENSION_ID=$(openssl rsa -in "$PEM_FILE" -pubout -outform DER 2>/dev/null \
    | sha256sum \
    | head -c 32 \
    | tr '0-9a-f' 'a-p')
echo "[build-ext] Extension ID: $EXTENSION_ID"

# 5. Get version
VERSION=$(python3 -c "import json; print(json.load(open('$EXT_DIR/manifest.json'))['version'])")

# 6. Skip external_crx JSON and enterprise policy -- they conflict with
#    setup-extension.py which handles installation via Preferences (location=4).
#    The external_crx method causes "Error Loading Extension" popup on start.
#    The enterprise policy force_install uses stale wsPort from build time.
#    setup-extension.py patches wsPort/desktopId dynamically per container.

# 7. Set ownership
chown -R screenbox:screenbox "$CRX_FILE" "$PEM_FILE" 2>/dev/null || true

echo "[build-ext] Extension setup complete"
