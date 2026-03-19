"""Application catalog for desktop_install/uninstall."""

APP_CATALOG = {
    "firefox": {"packages": ["firefox"], "description": "Mozilla Firefox browser"},
    "libreoffice": {"packages": ["libreoffice-calc", "libreoffice-writer", "libreoffice-impress"],
                    "description": "LibreOffice suite (Calc, Writer, Impress)"},
    "vscode": {"packages": [], "description": "Visual Studio Code",
               "install_script": (
                   "curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /tmp/ms.gpg "
                   "&& install -o root -g root -m 644 /tmp/ms.gpg /usr/share/keyrings/microsoft-archive-keyring.gpg "
                   "&& echo 'deb [signed-by=/usr/share/keyrings/microsoft-archive-keyring.gpg] "
                   "https://packages.microsoft.com/repos/code stable main' > /etc/apt/sources.list.d/vscode.list "
                   "&& apt-get update -qq && apt-get install -y --no-install-recommends code"
               )},
    "terminal": {"packages": ["xterm"], "description": "XTerm terminal emulator"},
    "file-manager": {"packages": ["pcmanfm"], "description": "PCManFM file manager"},
    "text-editor": {"packages": ["mousepad"], "description": "Mousepad text editor"},
    "calculator": {"packages": ["galculator"], "description": "Galculator"},
    "image-viewer": {"packages": ["eom"], "description": "Eye of MATE image viewer"},
    "nodejs": {"packages": ["nodejs", "npm"], "description": "Node.js runtime + npm"},
    "python-pip": {"packages": ["python3-pip"], "description": "Python pip package manager"},
    "git": {"packages": ["git"], "description": "Git version control"},
    "htop": {"packages": ["htop"], "description": "Interactive process viewer"},
    "vim": {"packages": ["vim"], "description": "Vim text editor"},
}
