FROM debian:bookworm-slim

LABEL org.screenbox.component="desktop"
LABEL org.screenbox.desktop.variant="xfce"

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV SCREEN_WIDTH=1920
ENV SCREEN_HEIGHT=1080
ENV SCREEN_DEPTH=24

# System packages + Xvnc (TigerVNC) + xrdp (replaces KasmVNC)
# Xvnc = X server with built-in VNC (cursor sent via protocol, not baked into pixels)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xdotool \
    xclip \
    wmctrl \
    imagemagick \
    curl \
    wget \
    ca-certificates \
    fonts-liberation \
    fonts-cantarell \
    fonts-noto-color-emoji \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    net-tools \
    openssl \
    gosu \
    dbus-x11 \
    tigervnc-standalone-server \
    xrdp \
    && rm -rf /var/lib/apt/lists/*

# XFCE desktop (lightweight, ~400MB vs MATE ~1GB)
RUN apt-get update && apt-get install -y --no-install-recommends \
    xfce4-panel \
    xfce4-session \
    xfce4-settings \
    xfce4-terminal \
    xfwm4 \
    xfdesktop4 \
    thunar \
    mousepad \
    arc-theme \
    htop \
    && rm -rf /var/lib/apt/lists/*

# Python3 (for ws-bridge and scripts) + dconf-cli (for panel reset)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-websocket \
    zip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# AT-SPI accessibility (for OS-level automation: LibreOffice, file manager, dialogs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    at-spi2-core \
    libatk-adaptor \
    python3-gi \
    python3-cairo \
    python3-gi-cairo \
    gir1.2-atspi-2.0 \
    gir1.2-gtk-3.0 \
    && rm -rf /var/lib/apt/lists/*

# xrdp config: auto-connect to x11vnc on localhost:5900, no login screen
RUN sed -i 's/^autorun=.*/autorun=screenbox/' /etc/xrdp/xrdp.ini || true && \
    printf '\n[screenbox]\nname=Screenbox Desktop\nlib=libvnc.so\nip=127.0.0.1\nport=5900\nusername=ask\npassword=ask\n' >> /etc/xrdp/xrdp.ini && \
    # Fix log paths to absolute (xrdp runs as non-root screenbox user)
    sed -i 's|^LogFile=.*|LogFile=/var/log/screenbox/xrdp.log|' /etc/xrdp/xrdp.ini && \
    sed -i 's|^LogFile=.*|LogFile=/var/log/screenbox/xrdp-sesman.log|' /etc/xrdp/sesman.ini 2>/dev/null || true && \
    # Disable sesman (not needed -- we auto-connect to VNC, no session management)
    sed -i 's/^ForkSessions=.*/ForkSessions=false/' /etc/xrdp/sesman.ini 2>/dev/null || true

# Chromium (from Debian repo)
RUN echo "deb http://deb.debian.org/debian bookworm main" > /etc/apt/sources.list.d/debian-bookworm.list \
    && apt-get update && apt-get install -y --no-install-recommends -t bookworm \
    chromium \
    && ln -sf /usr/bin/chromium /usr/bin/chromium-browser \
    && rm -f /etc/apt/sources.list.d/debian-bookworm.list \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -s /bin/bash -u 1000 screenbox

# Extension files (copy as root for CRX build)
COPY extension/ /opt/screenbox/extension/
COPY build-and-install-extension.sh /tmp/build-and-install-extension.sh
RUN chmod +x /tmp/build-and-install-extension.sh && /tmp/build-and-install-extension.sh && rm /tmp/build-and-install-extension.sh \
    && chown -R screenbox:screenbox /home/screenbox/ \
    && chown -R screenbox:screenbox /opt/screenbox/extension/

# XFCE config: defaults in /opt (survive bind mounts), also pre-populate .config
COPY xfce/xfconf/ /opt/screenbox/xfce-defaults/xfconf/
COPY xfce/gtk-3.0/ /opt/screenbox/xfce-defaults/gtk-3.0/
COPY xfce/xfconf/ /home/screenbox/.config/xfce4/xfconf/
COPY xfce/gtk-3.0/gtk.css /home/screenbox/.config/gtk-3.0/gtk.css
COPY xfce/themes/Screenbox/ /usr/share/themes/Screenbox/
COPY mate/google-chrome.desktop /usr/share/applications/chromium-browser.desktop
# Clean up app menu: hide broken entries, configure defaults
RUN \
    # Hide broken/irrelevant menu entries
    for f in display-im6.q16 xfce4-mail-reader; do \
        echo 'NoDisplay=true' >> /usr/share/applications/$f.desktop 2>/dev/null || true; \
    done && \
    # Configure exo-open default apps (stored in xfce-defaults to survive bind mounts)
    printf '[Configuration]\nWebBrowser=chromium\nTerminalEmulator=xfce4-terminal\nFileManager=thunar\n' \
        > /opt/screenbox/xfce-defaults/helpers.rc && \
    # Wrap chromium binary to always include --no-sandbox in Docker
    mv /usr/bin/chromium /usr/bin/chromium-real && \
    printf '#!/bin/sh\nexec /usr/bin/chromium-real --no-sandbox --test-type "$@"\n' > /usr/bin/chromium && \
    chmod +x /usr/bin/chromium
RUN chown -R screenbox:screenbox /home/screenbox/.config /opt/screenbox/xfce-defaults

# xrdp needs writable dirs at runtime
RUN mkdir -p /var/run/xrdp /var/log/xrdp && \
    chown -R screenbox:screenbox /var/run/xrdp /var/log/xrdp /etc/xrdp

# Directories + desktop shortcuts
RUN mkdir -p /home/screenbox/downloads /home/screenbox/workspace \
    /home/screenbox/Desktop /home/screenbox/Desktop/Documents && \
    chown -R screenbox:screenbox /home/screenbox/downloads /home/screenbox/workspace /home/screenbox/Desktop
# Desktop shortcuts: defaults (copied to Desktop at runtime if missing)
COPY mate/google-chrome.desktop /opt/screenbox/defaults/chromium-browser.desktop
# Also pre-populate Desktop for fresh containers
COPY mate/google-chrome.desktop /home/screenbox/Desktop/chromium-browser.desktop
RUN chmod +x /home/screenbox/Desktop/*.desktop /opt/screenbox/defaults/*.desktop && \
    chown -R screenbox:screenbox /home/screenbox/Desktop /opt/screenbox/defaults

# System scripts -> /opt/screenbox/bin/ (keep home dir clean)
COPY ws-bridge.py setup-extension.py click-indicator.py grid-overlay.py vision-watch.py entrypoint.sh start-chrome.sh /opt/screenbox/bin/
RUN chmod +x /opt/screenbox/bin/entrypoint.sh /opt/screenbox/bin/start-chrome.sh && \
    chown -R screenbox:screenbox /opt/screenbox/bin

WORKDIR /home/screenbox

# xrdp on 3389, ws-bridge on 8765
EXPOSE 3389 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD pgrep -x xrdp > /dev/null && pgrep -x Xvnc > /dev/null || exit 1

ENTRYPOINT ["/opt/screenbox/bin/entrypoint.sh"]
