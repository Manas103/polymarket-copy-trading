#!/usr/bin/env bash
# Deploy/update the copy trading bot to an Oracle Cloud VM.
# Usage: ./deploy/deploy.sh <VM_IP> [SSH_USER]
#
# Example:
#   ./deploy/deploy.sh 129.151.xx.xx ubuntu

set -euo pipefail

VM_IP="${1:?Usage: ./deploy/deploy.sh <VM_IP> [SSH_USER]}"
SSH_USER="${2:-ubuntu}"
REMOTE="$SSH_USER@$VM_IP"
APP_DIR="/opt/copytrade"

echo "=== Deploying to $REMOTE ==="

# 1. Sync project files (exclude secrets, DB, dev artifacts)
echo "Syncing files..."
rsync -avz --delete \
    --exclude '.env' \
    --exclude '*.db' \
    --exclude '*.db-wal' \
    --exclude '*.db-shm' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '*.egg-info/' \
    --exclude '.git/' \
    --exclude '.claude/' \
    --exclude 'tests/' \
    --exclude 'test_trade.py' \
    -e ssh \
    ./ "$REMOTE:$APP_DIR/"

# 2. Install deps and restart
echo "Installing dependencies and restarting service..."
ssh "$REMOTE" "sudo $APP_DIR/venv/bin/pip install --quiet -r $APP_DIR/requirements.txt && sudo systemctl restart copytrade"

# 3. Show status
echo ""
echo "=== Deploy complete ==="
ssh "$REMOTE" "sudo systemctl status copytrade --no-pager"
echo ""
echo "Dashboard: http://$VM_IP:8080"
echo "Logs:      ssh $REMOTE 'journalctl -u copytrade -f'"
