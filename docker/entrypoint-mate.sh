#!/bin/bash
# Screenbox MATE container entrypoint (Xvnc + xrdp)
# Xvnc (TigerVNC) = X server with built-in VNC on :5900, xrdp serves RDP on :3389
# Cursor sent via VNC protocol (not baked into framebuffer pixels = no duplicate cursor)
# Starts: Xvnc -> xrdp -> MATE -> ws-bridge
# Chrome (or any app) is optional -- this is a virtual DESKTOP.
# No set -e: explicit error checks only (set -e + background processes = silent crashes)

DISPLAY=${DISPLAY:-:99}
SCREEN_WIDTH=${SCREEN_WIDTH:-1920}
SCREEN_HEIGHT=${SCREEN_HEIGHT:-1080}
SCREEN_DEPTH=${SCREEN_DEPTH:-24}
CHROME_URL=${CHROME_URL:-none}
WS_BRIDGE_PORT=${WS_BRIDGE_PORT:-8765}

# Validate DESKTOP_ID to prevent command injection via sed/shell interpolation
if [ -n "$DESKTOP_ID" ] && [[ ! "$DESKTOP_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$ ]]; then
    echo "[screenbox] ERROR: Invalid DESKTOP_ID: $DESKTOP_ID" >&2
    exit 1
fi

# === Fix volume permissions (cross-platform: Linux/Windows/macOS) ===
if [ "$(id -u)" = "0" ]; then
    chown screenbox:screenbox /home/screenbox 2>/dev/null || true
    chown -R screenbox:screenbox /home/screenbox/.config 2>/dev/null || true
    chown -R screenbox:screenbox /home/screenbox/downloads 2>/dev/null || true
    chown -R screenbox:screenbox /home/screenbox/workspace 2>/dev/null || true
    chown -R screenbox:screenbox /home/screenbox/Desktop 2>/dev/null || true
    chown -R screenbox:screenbox /var/run/xrdp /var/log/xrdp /etc/xrdp 2>/dev/null || true
    mkdir -p /var/log/screenbox && chown screenbox:screenbox /var/log/screenbox

    echo "[screenbox] Starting MATE desktop ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH} on ${DISPLAY}"

    # Run the rest as screenbox user
    exec gosu screenbox "$0" "$@"
fi

# === Everything below runs as screenbox ===

export DISPLAY
export HOME=/home/screenbox

# Signal trap for crash diagnostics
_log_signal() {
    local sig=$1
    echo "[screenbox] SIGNAL RECEIVED: $sig (PID $$, $(date -Iseconds))" >&2
    echo "[screenbox] Memory: $(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo '?') / $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo '?')" >&2
    echo "[screenbox] Processes: $(ps aux --no-headers 2>/dev/null | wc -l)" >&2
}
trap '_log_signal SIGTERM; kill $XVNC_PID 2>/dev/null' SIGTERM
trap '_log_signal SIGINT; kill $XVNC_PID 2>/dev/null' SIGINT
trap '_log_signal SIGHUP' SIGHUP

# 1. D-Bus session bus (required by MATE and AT-SPI)
eval $(dbus-launch --sh-syntax) 2>/dev/null || true

# 2. Xvnc (TigerVNC: X server + built-in VNC on port 5900)
# Cursor sent via VNC protocol, not baked into framebuffer pixels
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
mkdir -p /tmp/.X11-unix

Xvnc ${DISPLAY} \
    -geometry ${SCREEN_WIDTH}x${SCREEN_HEIGHT} \
    -depth ${SCREEN_DEPTH} \
    -rfbport 5900 \
    -SecurityTypes None \
    -AlwaysShared \
    -AcceptKeyEvents \
    -AcceptPointerEvents \
    -localhost 0 \
    2>/var/log/screenbox/xvnc.log &
XVNC_PID=$!
sleep 1

if ! kill -0 $XVNC_PID 2>/dev/null; then
    echo "[screenbox] ERROR: Xvnc failed to start"
    cat /var/log/screenbox/xvnc.log 2>/dev/null
    exit 1
fi

# 3. xrdp (RDP server on port 3389, connects to Xvnc on 5900)
# Generate xrdp RSA key if missing
if [ ! -f /etc/xrdp/rsakeys.ini ]; then
    xrdp-keygen xrdp /etc/xrdp/rsakeys.ini 2>/dev/null || true
fi

xrdp --nodaemon --port 3389 2>&1 | tee /var/log/screenbox/xrdp.log &
XRDP_PID=$!
sleep 1

if ! kill -0 $XRDP_PID 2>/dev/null; then
    echo "[screenbox] ERROR: xrdp failed to start"
    cat /var/log/screenbox/xrdp.log 2>/dev/null
    exit 1
fi

# 4. AT-SPI accessibility bus (for OS-level automation)
export GTK_MODULES=atk-bridge
export NO_AT_BRIDGE=0
/usr/libexec/at-spi-bus-launcher --launch-immediately &
sleep 0.5
/usr/libexec/at-spi2-registryd --use-gnome-session &
sleep 0.3

# Persist env for docker exec sessions
cat > /home/screenbox/.dbus-env << ENVEOF
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS}"
export DISPLAY="${DISPLAY}"
export GTK_MODULES=atk-bridge
export NO_AT_BRIDGE=0
ENVEOF

# 5. Apply MATE config defaults (bind mounts overwrite image configs on first run)
if [ ! -f "$HOME/.config/apply-theme.sh" ]; then
    # Config dir is empty (bind mount) -- copy defaults from image
    cp -f /usr/share/glib-2.0/schemas/90_screenbox.gschema.override "$HOME/.config/" 2>/dev/null || true
    # Copy apply-theme.sh from the MATE defaults baked into image
    for f in apply-theme.sh google-chrome.desktop terminal.desktop screenbox.layout; do
        [ -f "/opt/screenbox/defaults/$f" ] && cp -f "/opt/screenbox/defaults/$f" "$HOME/.config/$f" 2>/dev/null || true
    done
fi

# Apply theme (before session starts)
if [ -f "$HOME/.config/apply-theme.sh" ]; then
    bash "$HOME/.config/apply-theme.sh" 2>/dev/null || true
fi

# 5a. Generate screenbox wallpaper (shared path for both XFCE and MATE)
WALLPAPER="/home/screenbox/.config/screenbox-wallpaper.png"
if [ ! -f "$WALLPAPER" ]; then
    convert -size ${SCREEN_WIDTH}x${SCREEN_HEIGHT} xc:'#0a0e14' \
        -font Cantarell-Thin -pointsize 120 -kerning 18 -fill '#1c2030' -gravity center \
        -annotate +0+0 'screenbox.dev' \
        "$WALLPAPER" 2>/dev/null || true
fi

# 5b. Clean stale Chromium locks (bind mounts persist locks across container recreations)
rm -f "$HOME/.config/chromium/SingletonLock" "$HOME/.config/chromium/SingletonSocket" "$HOME/.config/chromium/SingletonCookie" 2>/dev/null || true
rm -f "$HOME/.config/chromium-profile/SingletonLock" "$HOME/.config/chromium-profile/SingletonSocket" "$HOME/.config/chromium-profile/SingletonCookie" 2>/dev/null || true

# 6. MATE desktop session
mate-session 2>/dev/null &
MATE_PID=$!
sleep 3

# 6a. Set wallpaper via dconf (survives session restarts, overrides gschema)
dconf write /org/mate/desktop/background/picture-filename "'${WALLPAPER}'"
dconf write /org/mate/desktop/background/picture-options "'stretched'"
dconf write /org/mate/desktop/background/primary-color "'#0a0e14'"
dconf write /org/mate/desktop/background/show-desktop-icons true

# Ensure desktop shortcuts exist (may be missing if Desktop is bind-mounted)
mkdir -p "$HOME/Desktop/Documents"
for f in terminal.desktop chromium-browser.desktop; do
    [ ! -f "$HOME/Desktop/$f" ] && cp "/opt/screenbox/defaults/$f" "$HOME/Desktop/$f" 2>/dev/null || true
done
chmod +x "$HOME/Desktop"/*.desktop 2>/dev/null || true

# Desktop icons: position + mark as trusted
gio set 'file:///home/screenbox/Desktop/Documents' metadata::caja-icon-position '40,60' 2>/dev/null || true
gio set 'file:///home/screenbox/Desktop/terminal.desktop' metadata::caja-icon-position '40,170' 2>/dev/null || true
gio set 'file:///home/screenbox/Desktop/chromium-browser.desktop' metadata::caja-icon-position '40,280' 2>/dev/null || true
gio set /home/screenbox/Desktop/terminal.desktop metadata::trusted true 2>/dev/null || true
gio set /home/screenbox/Desktop/chromium-browser.desktop metadata::trusted true 2>/dev/null || true

# 6b. Force desktop icon settings (Computer off, Trash on, no Home/Volumes/Network)
dconf write /org/mate/caja/desktop/computer-icon-visible false
dconf write /org/mate/caja/desktop/trash-icon-visible false
dconf write /org/mate/caja/desktop/home-icon-visible false
dconf write /org/mate/caja/desktop/volumes-visible false
dconf write /org/mate/caja/desktop/network-icon-visible false

# 6c. Force-load panel config (dconf write is more reliable than dconf load in containers)
dconf reset -f /org/mate/panel/

# General
dconf write /org/mate/panel/general/toplevel-id-list "['bottom']"
dconf write /org/mate/panel/general/object-id-list "['main-menu', 'window-list', 'clock']"
dconf write /org/mate/panel/general/default-layout "'screenbox'"

# Bottom panel
dconf write /org/mate/panel/toplevels/bottom/expand true
dconf write /org/mate/panel/toplevels/bottom/orientation "'bottom'"
dconf write /org/mate/panel/toplevels/bottom/size 28
dconf write /org/mate/panel/toplevels/bottom/monitor 0

# Main menu (left)
dconf write /org/mate/panel/objects/main-menu/object-type "'menu'"
dconf write /org/mate/panel/objects/main-menu/toplevel-id "'bottom'"
dconf write /org/mate/panel/objects/main-menu/position 0
dconf write /org/mate/panel/objects/main-menu/use-menu-path false

# Window list (center)
dconf write /org/mate/panel/objects/window-list/object-type "'applet'"
dconf write /org/mate/panel/objects/window-list/applet-iid "'WnckletFactory::WindowListApplet'"
dconf write /org/mate/panel/objects/window-list/toplevel-id "'bottom'"
dconf write /org/mate/panel/objects/window-list/position 1

# Clock (right)
dconf write /org/mate/panel/objects/clock/object-type "'applet'"
dconf write /org/mate/panel/objects/clock/applet-iid "'ClockAppletFactory::ClockApplet'"
dconf write /org/mate/panel/objects/clock/toplevel-id "'bottom'"
dconf write /org/mate/panel/objects/clock/position 0
dconf write /org/mate/panel/objects/clock/panel-right-stick true

mate-panel --replace &
sleep 1

# 6d. Click indicator (auto-restart on crash)
(while true; do
    python3 /opt/screenbox/bin/click-indicator.py 2>&1 | tee /var/log/screenbox/click-indicator.log
    sleep 1
done) &
sleep 0.5

# 6e. Grid overlay (auto-restart on crash)
(while true; do
    python3 /opt/screenbox/bin/grid-overlay.py 2>&1 | tee /var/log/screenbox/grid-overlay.log
    sleep 1
done) &
sleep 0.3

# 7. WS bridge (Chrome extension <-> external controller)
# Generate per-desktop authentication token for ws-bridge
if [ -z "$SCREENBOX_WS_TOKEN" ]; then
    export SCREENBOX_WS_TOKEN=$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 16)
fi
python3 /opt/screenbox/bin/ws-bridge.py ${WS_BRIDGE_PORT} 2>&1 | tee /var/log/screenbox/ws-bridge.log &
WS_BRIDGE_PID=$!
sleep 0.2

# 8. Chrome (optional -- default: none, no auto-start)
if [ "$CHROME_URL" != "none" ]; then
    CHROME_PROFILE=${CHROME_PROFILE:-/home/screenbox/.config/chromium-profile}

    rm -f "${CHROME_PROFILE}/SingletonLock" "${CHROME_PROFILE}/SingletonSocket" "${CHROME_PROFILE}/SingletonCookie" 2>/dev/null || true

    # Patch extension config with current wsPort/desktopId
    # DESKTOP_ID is validated at startup (alphanumeric + hyphen/underscore only)
    # WS_BRIDGE_PORT is cast to int. Using pipe delimiter in sed to avoid injection.
    EXT_PATH="/opt/screenbox/extension"
    if [ -f "${EXT_PATH}/background.js" ]; then
        sed -i "s|let wsPort = .*|let wsPort = ${WS_BRIDGE_PORT};|" "${EXT_PATH}/background.js"
        sed -i "s|let desktopId = .*|let desktopId = '${DESKTOP_ID}';|" "${EXT_PATH}/background.js"
        sed -i "s|let wsToken = .*|let wsToken = '${SCREENBOX_WS_TOKEN}';|" "${EXT_PATH}/background.js"
        echo "[screenbox] Extension patched: wsPort=${WS_BRIDGE_PORT}, desktopId=${DESKTOP_ID}, token=${SCREENBOX_WS_TOKEN:0:4}..."
    fi

    # Register extension in Chrome Preferences (copies to profile, sets location=4)
    if [ -f /opt/screenbox/bin/setup-extension.py ]; then
        python3 /opt/screenbox/bin/setup-extension.py
    fi

    # Clear Chrome Service Worker cache (stale cached background.js has wrong token)
    rm -rf "${CHROME_PROFILE}/Default/Service Worker" \
           "${CHROME_PROFILE}/Default/Code Cache" \
           "${CHROME_PROFILE}/Default/ScriptCache" 2>/dev/null || true

    # Build extension flags
    LOAD_EXT_FLAGS=""
    if [ -d "${EXT_PATH}" ]; then
        LOAD_EXT_FLAGS="--load-extension=${EXT_PATH} --disable-extensions-except=${EXT_PATH}"
        echo "[screenbox] Loading extension from ${EXT_PATH}"
    fi

    /usr/bin/chromium \
        --user-data-dir="${CHROME_PROFILE}" \
        --no-first-run \
        --disable-sync \
        --disable-default-apps \
        --hide-crash-restore-bubble \
        --password-store=basic \
        --remote-allow-origins=http://127.0.0.1:9222 \
        --remote-debugging-port=9222 \
        --remote-debugging-address=127.0.0.1 \
        --force-renderer-accessibility \
        --disable-gpu \
        --start-maximized \
        ${LOAD_EXT_FLAGS} \
        "${CHROME_URL}" 2>/var/log/screenbox/chrome.log &
    CHROME_PID=$!
fi

echo "[screenbox] Desktop ready (Xvnc + xrdp + MATE)"
echo "[screenbox]   RDP:       port 3389"
echo "[screenbox]   WS bridge: port ${WS_BRIDGE_PORT}"

# Wait for Xvnc (anchor process)
echo "[screenbox] Waiting for Xvnc (PID $XVNC_PID) as anchor process"
while kill -0 $XVNC_PID 2>/dev/null; do
    wait $XVNC_PID 2>/dev/null || true
done

EXIT_CODE=$?
echo "[screenbox] Xvnc exited (code=$EXIT_CODE), shutting down"
echo "[screenbox] Memory at exit: $(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo '?') / $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo '?')"
kill $XRDP_PID $MATE_PID $WS_BRIDGE_PID ${CHROME_PID:-} 2>/dev/null || true
wait
