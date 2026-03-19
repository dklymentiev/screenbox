#!/bin/bash
# Start Chromium with Screenbox extension.
# Used by: entrypoint.sh (initial start) and app_launch (restart after crash).
# Args: [URL] (default: about:blank)

CHROME_URL="${1:-about:blank}"
CHROME_PROFILE="${CHROME_PROFILE:-/home/screenbox/.config/chromium}"
EXT_PATH="/opt/screenbox/extension"
DISPLAY="${DISPLAY:-:99}"
export DISPLAY

# Kill existing Chrome (clean restart)
# Use -x (exact name match) not -f (full cmdline) to avoid killing ourselves
pkill -x chromium 2>/dev/null || true
sleep 0.3

# Clean singleton locks (prevents "already running" errors)
rm -f "${CHROME_PROFILE}/SingletonLock" \
      "${CHROME_PROFILE}/SingletonSocket" \
      "${CHROME_PROFILE}/SingletonCookie" 2>/dev/null || true

# Clean stale service worker cache (prevents wrong ws-bridge token)
rm -rf "${CHROME_PROFILE}/Default/Service Worker" \
       "${CHROME_PROFILE}/Default/Code Cache" \
       "${CHROME_PROFILE}/Default/ScriptCache" 2>/dev/null || true

# Load Screenbox extension (required for Chrome semantics)
LOAD_EXT_FLAGS=""
if [ -d "${EXT_PATH}" ]; then
    LOAD_EXT_FLAGS="--load-extension=${EXT_PATH}"
fi

# Read profile Chrome flags if available
PROFILE_CHROME_FLAGS=""
PROFILE_USER_AGENT=""
if [ -f "$PROFILE_FILE" ] && command -v python3 >/dev/null 2>&1; then
    PROFILE_USER_AGENT=$(python3 -c "import json; print(json.load(open('$PROFILE_FILE')).get('browser',{}).get('user_agent',''))" 2>/dev/null)
    PROF_LANG=$(python3 -c "import json; print(json.load(open('$PROFILE_FILE')).get('browser',{}).get('languages',''))" 2>/dev/null)
    [ -n "$PROF_LANG" ] && PROFILE_CHROME_FLAGS="$PROFILE_CHROME_FLAGS --lang=$PROF_LANG"
    PROF_DPR=$(python3 -c "import json; print(json.load(open('$PROFILE_FILE')).get('browser',{}).get('pixel_ratio',''))" 2>/dev/null)
    [ -n "$PROF_DPR" ] && [ "$PROF_DPR" != "1.0" ] && [ "$PROF_DPR" != "1" ] && PROFILE_CHROME_FLAGS="$PROFILE_CHROME_FLAGS --force-device-scale-factor=$PROF_DPR"
fi

# Detect browser: prefer Google Chrome over Chromium
CHROME_BIN="/usr/bin/chromium"
CHROME_EXTRA_FLAGS=""
if command -v google-chrome-stable >/dev/null 2>&1; then
    CHROME_BIN="/usr/bin/google-chrome-stable"
    CHROME_EXTRA_FLAGS="--no-sandbox --disable-infobars"
    CHROME_PROFILE="/home/screenbox/.config/google-chrome"
fi

CHROME_UA_FLAG=""
if [ -n "${PROFILE_USER_AGENT}" ]; then
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
    "${CHROME_URL}" 2>/var/log/screenbox/chrome.log &

echo $!
