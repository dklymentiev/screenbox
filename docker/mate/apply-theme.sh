#!/bin/bash
# Apply MATE theme -- identical to host Ubuntu MATE server
# Called from entrypoint after dbus-launch and before mate-session

# GTK theme -- Yaru-sage-dark
gsettings set org.mate.interface gtk-theme 'Yaru-sage-dark'
gsettings set org.mate.interface icon-theme 'Yaru-sage-dark'
gsettings set org.mate.interface font-name 'Liberation Sans 10'
gsettings set org.mate.interface monospace-font-name 'Liberation Mono 11'

# Window manager
gsettings set org.mate.Marco.general theme 'Yaru-sage-dark'
gsettings set org.mate.Marco.general titlebar-font 'Liberation Sans Bold 10'
gsettings set org.mate.Marco.general num-workspaces 1

# Desktop background -- screenbox wallpaper (set via gschema override, reinforce here)
gsettings set org.mate.background picture-filename '/home/screenbox/.config/screenbox-wallpaper.png'
gsettings set org.mate.background picture-options 'stretched'
gsettings set org.mate.background primary-color '#0a0e14'
gsettings set org.mate.background show-desktop-icons true

# Panel layout -- single bottom panel with one menu button
gsettings set org.mate.panel default-layout 'screenbox'

# Terminal
gsettings set org.mate.terminal.profile:/org/mate/terminal/profiles/default/ background-color '#1a1a2e'
gsettings set org.mate.terminal.profile:/org/mate/terminal/profiles/default/ foreground-color '#e0e0e0'
gsettings set org.mate.terminal.profile:/org/mate/terminal/profiles/default/ use-theme-colors false
gsettings set org.mate.terminal.profile:/org/mate/terminal/profiles/default/ font 'Liberation Mono 11'

echo "[screenbox] Theme applied: Yaru-sage-dark"
