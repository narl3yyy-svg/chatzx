#!/usr/bin/env bash
set -e

echo "chatxz - Arch Linux Installer"
echo "=============================="

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo "Please run as normal user (not root)."
    exit 1
fi

# Install system dependencies
echo "Installing system dependencies..."
sudo pacman -S --needed --noconfirm python python-pip python-setuptools base-devel

# Optional: voice support
read -p "Install voice support (pyaudio)? [y/N]: " voice_opt
if [[ "$voice_opt" =~ ^[Yy]$ ]]; then
    sudo pacman -S --needed --noconfirm portaudio
fi

# Optional: image display in terminal
read -p "Install terminal image viewer (chafa) for screenshot preview? [y/N]: " img_opt
if [[ "$img_opt" =~ ^[Yy]$ ]]; then
    sudo pacman -S --needed --noconfirm chafa
fi

# Install chatxz
echo "Installing chatxz..."
pip install --user .

# Install system-wide or user-level
read -p "Install system-wide (requires sudo) instead of user? [y/N]: " sys_opt
if [[ "$sys_opt" =~ ^[Yy]$ ]]; then
    sudo pip install .
fi

echo ""
echo "chatxz installed successfully!"
echo "Run 'chatxz --help' to get started."
echo "Your config will be stored in ~/.config/chatxz/"
