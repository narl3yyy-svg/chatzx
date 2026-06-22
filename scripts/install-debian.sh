#!/usr/bin/env bash
set -e

echo "chatxz - Debian/Ubuntu Installer"
echo "================================"

if [ "$EUID" -eq 0 ]; then
    echo "Please run as normal user (not root)."
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
sudo apt update
sudo apt install -y python3 python3-pip python3-setuptools python3-dev build-essential

# Optional: voice support
read -p "Install voice support (pyaudio)? [y/N]: " voice_opt
if [[ "$voice_opt" =~ ^[Yy]$ ]]; then
    sudo apt install -y portaudio19-dev python3-pyaudio
fi

# Optional: image display in terminal
read -p "Install terminal image viewer (chafa) for screenshot preview? [y/N]: " img_opt
if [[ "$img_opt" =~ ^[Yy]$ ]]; then
    sudo apt install -y chafa
fi

# Install chatxz
echo "Installing chatxz..."
pip3 install --user .

read -p "Install system-wide (requires sudo) instead of user? [y/N]: " sys_opt
if [[ "$sys_opt" =~ ^[Yy]$ ]]; then
    sudo pip3 install .
fi

echo ""
echo "Setting up serial port permissions (dialout group)..."
bash "$(dirname "$0")/setup-serial-perms.sh" || true

echo ""
echo "chatxz installed successfully!"
echo "Run 'chatxz --help' to get started."
echo "Your config will be stored in ~/.config/chatxz/"
