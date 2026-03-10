#!/usr/bin/env bash
# One-time VM setup for Polymarket Copy Trading bot.
# Run as root on a fresh Ubuntu 22.04/24.04 ARM instance.
# Safe to re-run (idempotent).

set -euo pipefail

APP_DIR="/opt/copytrade"
DATA_DIR="$APP_DIR/data"
LOG_DIR="/var/log/copytrade"
VENV_DIR="$APP_DIR/venv"
SERVICE_USER="copytrade"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Polymarket Copy Trading — VM Setup ==="

# 1. Install Python 3.12
if ! command -v python3.12 &>/dev/null; then
    echo "Installing Python 3.12..."
    apt-get update -qq
    apt-get install -y software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get update -qq
    apt-get install -y python3.12 python3.12-venv python3.12-dev
else
    echo "Python 3.12 already installed."
fi

# 2. Create system user
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "Creating user $SERVICE_USER..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    echo "User $SERVICE_USER already exists."
fi

# 3. Create directories
echo "Creating directories..."
mkdir -p "$APP_DIR" "$DATA_DIR" "$LOG_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR" "$LOG_DIR"

# 4. Create virtual environment
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python venv..."
    python3.12 -m venv "$VENV_DIR"
else
    echo "Venv already exists."
fi

# 5. Install dependencies
echo "Installing pip dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# 6. Install systemd service
echo "Installing systemd service..."
cp "$SCRIPT_DIR/copytrade.service" /etc/systemd/system/copytrade.service
systemctl daemon-reload
systemctl enable copytrade.service

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Create $APP_DIR/.env with your credentials (see deploy/env.example)"
echo "  2. Start the service:  systemctl start copytrade"
echo "  3. Check logs:         journalctl -u copytrade -f"
