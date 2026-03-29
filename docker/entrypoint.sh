#!/bin/bash
# Screenbox container entrypoint (Xvnc + xrdp)
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

    echo "[screenbox] Starting Xvnc+xrdp desktop ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH} on ${DISPLAY}"

    # Auto-restore user-installed packages AS ROOT (before dropping to screenbox)
    INSTALLED_PKGS="/home/screenbox/.screenbox/installed-packages.txt"
    if [ -f "$INSTALLED_PKGS" ]; then
        MISSING=""
        while IFS= read -r pkg; do
            [ -z "$pkg" ] && continue
            dpkg -s "$pkg" >/dev/null 2>&1 || MISSING="$MISSING $pkg"
        done < "$INSTALLED_PKGS"
        if [ -n "$MISSING" ]; then
            echo "[screenbox] Restoring packages:$MISSING"
            (
                for i in $(seq 1 36); do
                    if timeout 3 bash -c "echo > /dev/tcp/8.8.8.8/53" 2>/dev/null; then
                        break
                    fi
                    sleep 5
                done
                apt-get update -qq 2>/dev/null
                apt-get install -y -qq --no-install-recommends $MISSING 2>/dev/null
                if [ $? -eq 0 ]; then
                    echo "[screenbox] Package restore complete"
                else
                    echo "[screenbox] Package restore FAILED for:$MISSING"
                fi
            ) &
        fi
    fi

    # Start gnome-keyring for Chrome cookie encryption persistence.
    # Uses empty password = auto-unlock. Keyring data stored in volume (~/.local/share/keyrings/).
    if command -v gnome-keyring-daemon >/dev/null 2>&1; then
        mkdir -p /home/screenbox/.local/share/keyrings
        chown -R screenbox:screenbox /home/screenbox/.local/share/keyrings
        echo -n "" | gosu screenbox gnome-keyring-daemon --unlock --replace --components=secrets 2>/dev/null &
        sleep 1
    fi

    # Restore browser data from backup (shutdown backup has WAL-checkpointed history/cookies)
    for profile_dir in /home/screenbox/.config/google-chrome /home/screenbox/.config/chromium; do
        backup_dir="$profile_dir/.screenbox-backup"
        if [ -d "$backup_dir" ]; then
            for f in "$backup_dir"/*; do
                name=$(basename "$f")
                target="$profile_dir/Default/$name"
                if [ -f "$f" ]; then
                    cp -f "$f" "$target" 2>/dev/null
                    chown screenbox:screenbox "$target" 2>/dev/null
                fi
            done
            # Mark Chrome as cleanly exited so it doesn't wipe restored history
            python3 -c "
import json, os
prefs_path = os.path.join('$profile_dir', 'Default', 'Preferences')
if os.path.isfile(prefs_path):
    prefs = json.load(open(prefs_path))
    prefs.setdefault('profile', {})['exit_type'] = 'Normal'
    prefs['profile']['exited_cleanly'] = True
    json.dump(prefs, open(prefs_path, 'w'))
" 2>/dev/null || true
            echo "[screenbox] Restored browser data from backup ($profile_dir)"
        fi
    done

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
_graceful_shutdown() {
    _log_signal "$1"
    # Backup browser databases WHILE Chrome is still running
    # Uses same nolock + sqlite3.backup approach as _browser_backup loop
    python3 -c "
import sqlite3, shutil, os
for profile in ['/home/screenbox/.config/google-chrome', '/home/screenbox/.config/chromium']:
    default = os.path.join(profile, 'Default')
    if not os.path.isdir(default): continue
    backup_dir = os.path.join(profile, '.screenbox-backup')
    os.makedirs(backup_dir, exist_ok=True)
    for name in ['History', 'Cookies', 'Login Data', 'Web Data', 'Bookmarks', 'Preferences']:
        src = os.path.join(default, name)
        if not os.path.isfile(src): continue
        dst = os.path.join(backup_dir, name)
        try:
            src_conn = sqlite3.connect('file:' + src + '?mode=ro&nolock=1', uri=True, timeout=5)
            dst_conn = sqlite3.connect(dst)
            src_conn.backup(dst_conn)
            if name == 'History':
                count = dst_conn.execute('SELECT COUNT(*) FROM urls').fetchone()[0]
                dst_conn.close()
                src_conn.close()
                if count == 0:
                    os.remove(dst)
                    continue
            else:
                dst_conn.close()
            src_conn.close()
        except:
            shutil.copy2(src, dst)
" 2>/dev/null || true
    # Now kill Chrome
    pkill -TERM -f 'chrome|chromium' 2>/dev/null
    for i in 1 2 3 4 5; do
        pgrep -f 'chrome|chromium' >/dev/null 2>&1 || break
        sleep 1
    done
    pkill -9 -f 'chrome|chromium' 2>/dev/null || true
    kill $XVNC_PID 2>/dev/null
}
trap '_graceful_shutdown SIGTERM' SIGTERM
trap '_graceful_shutdown SIGINT' SIGINT
trap '_log_signal SIGHUP' SIGHUP

# 1. D-Bus session bus (required by MATE and AT-SPI)
eval $(dbus-launch --sh-syntax) 2>/dev/null || true

# 1b. Unified browser profile: migrate Google Chrome → Chromium if needed
# Only migrate if Google Chrome binary is NOT installed (image switched to Chromium-only)
CHROMIUM_PROFILE="/home/screenbox/.config/chromium"
CHROME_PROFILE_OLD="/home/screenbox/.config/google-chrome"
if [ ! -x /usr/bin/google-chrome-stable ] && [ ! -x /usr/bin/google-chrome ]; then
    # Chrome not installed -- migrate profile to Chromium
    if [ -d "$CHROME_PROFILE_OLD" ] && [ ! -d "$CHROMIUM_PROFILE" ]; then
        echo "[screenbox] Migrating Google Chrome profile to Chromium path"
        mv "$CHROME_PROFILE_OLD" "$CHROMIUM_PROFILE"
    elif [ -d "$CHROME_PROFILE_OLD" ] && [ -d "$CHROMIUM_PROFILE" ]; then
        echo "[screenbox] Merging Google Chrome profile into Chromium"
        cp -rn "$CHROME_PROFILE_OLD"/* "$CHROMIUM_PROFILE"/ 2>/dev/null || true
        rm -rf "$CHROME_PROFILE_OLD"
    fi
fi
export CHROME_PROFILE="$CHROMIUM_PROFILE"

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

# 5. Generate screenbox wallpaper (shared path for both XFCE and MATE)
WALLPAPER="/home/screenbox/.config/screenbox-wallpaper.png"
if [ ! -f "$WALLPAPER" ]; then
    convert -size ${SCREEN_WIDTH}x${SCREEN_HEIGHT} xc:'#0a0e14' \
        -font Cantarell-Thin -pointsize 120 -kerning 18 -fill '#1c2030' -gravity center \
        -annotate +0+0 'screenbox.dev' \
        "$WALLPAPER" 2>/dev/null || true
fi

# 5a. Clean stale Chromium locks (bind mounts persist locks across container recreations)
rm -f "$HOME/.config/chromium/SingletonLock" "$HOME/.config/chromium/SingletonSocket" "$HOME/.config/chromium/SingletonCookie" 2>/dev/null || true

# 5b. Apply XFCE config (always -- bind mounts overwrite image configs on first run)
XFCE_DEFAULTS="/opt/screenbox/xfce-defaults"
if [ -d "$XFCE_DEFAULTS" ]; then
    mkdir -p "$HOME/.config/xfce4/xfconf/xfce-perchannel-xml"
    # cp -n: don't overwrite existing user configs (preserves customizations across restarts)
    cp -n "$XFCE_DEFAULTS"/xfconf/xfce-perchannel-xml/*.xml \
       "$HOME/.config/xfce4/xfconf/xfce-perchannel-xml/" 2>/dev/null || true
    mkdir -p "$HOME/.config/gtk-3.0"
    cp -n "$XFCE_DEFAULTS/gtk-3.0/gtk.css" "$HOME/.config/gtk-3.0/gtk.css" 2>/dev/null || true
    cp -n "$XFCE_DEFAULTS/helpers.rc" "$HOME/.config/xfce4/helpers.rc" 2>/dev/null || true
fi

# 6. XFCE desktop session
startxfce4 2>/dev/null &
XFCE_PID=$!
sleep 3

# 6b. Apply screenbox wallpaper (xfdesktop overwrites XML config with defaults for VNC-0)
WALLPAPER="/home/screenbox/.config/screenbox-wallpaper.png"
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorVNC-0/workspace0/last-image -s "$WALLPAPER" --create --type string 2>/dev/null || true
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorVNC-0/workspace0/image-style -s 5 --create --type int 2>/dev/null || true
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorVNC-0/workspace0/color-style -s 0 2>/dev/null || true
# Same for monitorscreen (fallback monitor name)
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorscreen/workspace0/last-image -s "$WALLPAPER" --create --type string 2>/dev/null || true
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorscreen/workspace0/image-style -s 5 --create --type int 2>/dev/null || true
xfconf-query -c xfce4-desktop -p /backdrop/screen0/monitorscreen/workspace0/color-style -s 0 2>/dev/null || true

# Ensure desktop shortcuts exist (may be missing if Desktop is bind-mounted)
mkdir -p "$HOME/Desktop"
for f in chromium-browser.desktop; do
    [ ! -f "$HOME/Desktop/$f" ] && cp "/opt/screenbox/defaults/$f" "$HOME/Desktop/$f" 2>/dev/null || true
done
chmod +x "$HOME/Desktop"/*.desktop 2>/dev/null || true

# 6c. Click indicator (auto-restart on crash)
# Write default config so indicator starts rendering immediately
printf 'enabled=1\ncursor=1\ndots=1\ntrail=0' > /tmp/.click-indicator-config
(while true; do
    python3 /opt/screenbox/bin/click-indicator.py 2>&1 | tee /var/log/screenbox/click-indicator.log
    sleep 1
done) &
sleep 0.5

# 6d. Grid overlay (auto-restart on crash)
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

# 8. Chrome + Extension (always started -- extension required for semantic tools)
CHROME_PROFILE=${CHROME_PROFILE:-/home/screenbox/.config/chromium}

rm -f "${CHROME_PROFILE}/SingletonLock" "${CHROME_PROFILE}/SingletonSocket" "${CHROME_PROFILE}/SingletonCookie" 2>/dev/null || true

# Patch extension config with current wsPort/desktopId/token
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


# 9. Desktop Profile (optional)
# New path: shared volume via SCREENBOX_PROFILE env var
# Fallback: old path /opt/screenbox/profile.json (backward compat)
PROFILE_FILE="${SCREENBOX_PROFILE:-/opt/screenbox/profile.json}"
PROFILE_CHROME_FLAGS=""
if [ -f "$PROFILE_FILE" ]; then
    echo "[screenbox] Desktop Profile: loading from $PROFILE_FILE"

    # Container-level: timezone
    PROF_TZ=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); print(d.get('container',{}).get('timezone',''))" 2>/dev/null)
    if [ -n "$PROF_TZ" ]; then
        echo "$PROF_TZ" > /etc/timezone
        ln -sf "/usr/share/zoneinfo/$PROF_TZ" /etc/localtime 2>/dev/null
        export TZ="$PROF_TZ"
        echo "[screenbox] Profile timezone: $PROF_TZ"
    fi

    # Container-level: locale
    PROF_LOCALE=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); print(d.get('container',{}).get('locale',''))" 2>/dev/null)
    if [ -n "$PROF_LOCALE" ]; then
        export LANG="$PROF_LOCALE"
        export LC_ALL="$PROF_LOCALE"
        echo "[screenbox] Profile locale: $PROF_LOCALE"
    fi

    # Container-level: fonts (install if specified)
    PROF_FONTS=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); f=d.get('container',{}).get('fonts',[]); print(' '.join(f))" 2>/dev/null)
    if [ -n "$PROF_FONTS" ]; then
        echo "[screenbox] Profile fonts: $PROF_FONTS"
        apt-get update -qq && apt-get install -y -qq $PROF_FONTS >/dev/null 2>&1 &
    fi

fi

# 5b. Package restore moved to root section (before gosu) — see above

if [ -f "$PROFILE_FILE" ]; then
    # Browser-level: Chrome flags from profile
    # UA stored separately -- contains spaces, can't go into unquoted $FLAGS
    PROFILE_USER_AGENT=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); print(d.get('browser',{}).get('user_agent',''))" 2>/dev/null)

    PROF_LANG=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); print(d.get('browser',{}).get('languages',''))" 2>/dev/null)
    if [ -n "$PROF_LANG" ]; then
        PROFILE_CHROME_FLAGS="$PROFILE_CHROME_FLAGS --lang=$PROF_LANG"
    fi

    PROF_DPR=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); print(d.get('browser',{}).get('pixel_ratio',''))" 2>/dev/null)
    if [ -n "$PROF_DPR" ] && [ "$PROF_DPR" != "1.0" ] && [ "$PROF_DPR" != "1" ]; then
        PROFILE_CHROME_FLAGS="$PROFILE_CHROME_FLAGS --force-device-scale-factor=$PROF_DPR"
    fi

    # Network: proxy
    PROF_PROXY_TYPE=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); print(d.get('network',{}).get('proxy_type','direct'))" 2>/dev/null)
    if [ "$PROF_PROXY_TYPE" != "direct" ] && [ -n "$PROF_PROXY_TYPE" ]; then
        PROF_PROXY_HOST=$(python3 -c "import json; d=json.load(open('$PROFILE_FILE')); n=d.get('network',{}); print(f\"{n.get('proxy_type','socks5')}://{n.get('proxy_host','')}:{n.get('proxy_port','1080')}\")" 2>/dev/null)
        PROFILE_CHROME_FLAGS="$PROFILE_CHROME_FLAGS --proxy-server=$PROF_PROXY_HOST"
        echo "[screenbox] Profile proxy: $PROF_PROXY_HOST"
    fi
else
    echo "[screenbox] No desktop profile"
fi

# Clear Chrome Service Worker cache (stale cached background.js has wrong token)
CHROME_PROFILE=${CHROME_PROFILE:-/home/screenbox/.config/chromium}
rm -rf "${CHROME_PROFILE}/Default/Service Worker" \
       "${CHROME_PROFILE}/Default/Code Cache" \
       "${CHROME_PROFILE}/Default/ScriptCache" 2>/dev/null || true

# Build extension flags
LOAD_EXT_FLAGS=""
if [ -d "${EXT_PATH}" ]; then
    LOAD_EXT_FLAGS="--load-extension=${EXT_PATH}"
    echo "[screenbox] Loading extension: MCP"
fi

# Use about:blank if no URL specified
CHROME_START_URL="${CHROME_URL}"
if [ "$CHROME_START_URL" = "none" ]; then
    CHROME_START_URL="about:blank"
fi

# Detect browser: prefer Google Chrome over Chromium
CHROME_BIN="/usr/bin/chromium"
CHROME_EXTRA_FLAGS=""
if command -v google-chrome-stable >/dev/null 2>&1; then
    CHROME_BIN="/usr/bin/google-chrome-stable"
    CHROME_EXTRA_FLAGS="--no-sandbox --disable-infobars"
    CHROME_PROFILE="/home/screenbox/.config/google-chrome"
    echo "[screenbox] Using Google Chrome: ${CHROME_BIN}"
else
    echo "[screenbox] Using Chromium: ${CHROME_BIN}"
fi

# UA flag needs special handling (contains spaces)
CHROME_UA_FLAG=""
if [ -n "${PROFILE_USER_AGENT:-}" ]; then
    CHROME_UA_FLAG="--user-agent=${PROFILE_USER_AGENT}"
fi

${CHROME_BIN} \
    ${CHROME_EXTRA_FLAGS} \
    ${PROFILE_CHROME_FLAGS} \
    ${CHROME_UA_FLAG:+"$CHROME_UA_FLAG"} \
    --user-data-dir="${CHROME_PROFILE}" \
    --no-first-run \
    --hide-crash-restore-bubble \
    --password-store=basic \
    --remote-allow-origins=http://127.0.0.1:9222 \
    --remote-debugging-port=9222 \
    --remote-debugging-address=127.0.0.1 \
    --force-renderer-accessibility \
    --disable-gpu \
    --start-maximized \
    ${LOAD_EXT_FLAGS} \
    "${CHROME_START_URL}" 2>/var/log/screenbox/chrome.log &
CHROME_PID=$!

echo "[screenbox] Desktop ready (Xvnc + xrdp)"
echo "[screenbox]   RDP:       port 3389"
echo "[screenbox]   WS bridge: port ${WS_BRIDGE_PORT}"

# Browser data persistence: backup every 30s + restore on start
# Chrome does not flush History/Cookies to disk on SIGTERM. Periodic backup ensures survival.
_browser_backup() {
    while true; do
        sleep 30
        python3 -c "
import sqlite3, shutil, os
for profile in [os.path.expanduser('~/.config/google-chrome'), os.path.expanduser('~/.config/chromium')]:
    default = os.path.join(profile, 'Default')
    if not os.path.isdir(default): continue
    backup_dir = os.path.join(profile, '.screenbox-backup')
    os.makedirs(backup_dir, exist_ok=True)
    for name in ['History', 'Cookies', 'Login Data', 'Web Data', 'Bookmarks', 'Preferences']:
        src = os.path.join(default, name)
        if not os.path.isfile(src): continue
        # For History: only backup if it has actual entries (don't overwrite good backup with empty)
        dst = os.path.join(backup_dir, name)
        try:
            # Use SQLite backup API with nolock (Chrome holds exclusive lock)
            src_conn = sqlite3.connect('file:' + src + '?mode=ro&nolock=1', uri=True, timeout=5)
            dst_conn = sqlite3.connect(dst)
            src_conn.backup(dst_conn)
            # For History: only keep if it has actual entries
            if name == 'History':
                count = dst_conn.execute('SELECT COUNT(*) FROM urls').fetchone()[0]
                dst_conn.close()
                src_conn.close()
                if count == 0:
                    os.remove(dst)
                    continue
            else:
                dst_conn.close()
            src_conn.close()
        except:
            # Non-SQLite files (Bookmarks, Preferences): plain copy
            shutil.copy2(src, dst)
" 2>/dev/null || true
    done
}
# Background backup loop: save cookies every 30s so they survive Chrome exit.
_browser_backup &

# Wait for Xvnc (anchor process)
echo "[screenbox] Waiting for Xvnc (PID $XVNC_PID) as anchor process"
while kill -0 $XVNC_PID 2>/dev/null; do
    wait $XVNC_PID 2>/dev/null || true
done

EXIT_CODE=$?
echo "[screenbox] Xvnc exited (code=$EXIT_CODE), shutting down"
echo "[screenbox] Memory at exit: $(cat /sys/fs/cgroup/memory.current 2>/dev/null || echo '?') / $(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo '?')"
kill $XRDP_PID $XFCE_PID $WS_BRIDGE_PID $CLICK_IND_PID $GRID_PID ${CHROME_PID:-} 2>/dev/null || true
wait
