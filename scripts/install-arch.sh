#!/usr/bin/env bash
set -e

echo "chatxz - Arch Linux Installer"
echo "=============================="

if [ "$EUID" -eq 0 ]; then
    echo "Please run as normal user (not root)."
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
sudo pacman -S --needed --noconfirm python python-pip python-setuptools base-devel

read -p "Install Opus library for Rust media engine? [Y/n]: " opus_opt
if [[ ! "$opus_opt" =~ ^[Nn]$ ]]; then
    sudo pacman -S --needed --noconfirm opus
fi

read -p "Install terminal image viewer (chafa) for screenshot preview? [y/N]: " img_opt
if [[ "$img_opt" =~ ^[Yy]$ ]]; then
    sudo pacman -S --needed --noconfirm chafa
fi

# Detect PEP 668 (externally-managed-environment)
PYTHON=$(command -v python3 || command -v python)
PEP668=false
$PYTHON -m pip install --dry-run --user rns 2>&1 | grep -q "externally-managed" && PEP668=true

install_via_pipx() {
    if ! command -v pipx &>/dev/null; then
        sudo pacman -S --needed --noconfirm python-pipx
    fi
    pipx install .
}

install_via_venv() {
    local VENV_DIR="$HOME/.local/share/chatxz/venv"
    mkdir -p "$(dirname "$VENV_DIR")"
    $PYTHON -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install .
    echo
    echo "chatxz installed in virtualenv at $VENV_DIR"
    echo "Add this to your ~/.bashrc or ~/.zshrc:"
    echo "  export PATH=\"\$VENV_DIR/bin:\$PATH\""
    VENV_DIR="$VENV_DIR" envsubst < /dev/null 2>/dev/null || true
}

install_via_break() {
    echo "Using --break-system-packages (system-wide user install)..."
    pip install --user --break-system-packages .
}

if [ "$PEP668" = true ]; then
    echo
    echo "System Python uses PEP 668 (externally-managed-environment)."
    echo "Choose install method:"
    echo "  1) pipx  (recommended - isolated per-app environment)"
    echo "  2) venv  (virtual environment in ~/.local/share/chatxz/)"
    echo "  3) pip   (override with --break-system-packages)"
    read -p "Choice [1]: " method
    case "$method" in
        2) install_via_venv ;;
        3) install_via_break ;;
        *) install_via_pipx ;;
    esac
else
    pip install --user .
fi

echo ""
echo "Setting up serial port permissions (uucp/dialout groups)..."
bash "$(dirname "$0")/setup-serial-perms.sh" || true

echo ""
echo "chatxz installed!"
echo "Run 'chatxz --help' to get started."
