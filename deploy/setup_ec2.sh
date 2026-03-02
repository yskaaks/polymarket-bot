#!/usr/bin/env bash
# ============================================================
# EC2 Setup Script for Polymarket Bot
#
# Target: Amazon Linux 2023 on t3.micro (free tier eligible)
#
# Usage:
#   1. Launch a t3.micro EC2 instance with Amazon Linux 2023 AMI
#   2. SSH in: ssh -i your-key.pem ec2-user@<public-ip>
#   3. Clone repo: git clone <your-repo-url> ~/polymarket-bot
#   4. Run: cd ~/polymarket-bot && bash deploy/setup_ec2.sh
#   5. Edit .env: nano ~/polymarket-bot/.env
#   6. Start: sudo systemctl start polymarket-bot
#
# Security group: only needs outbound HTTPS (443) + WSS (443).
# No inbound ports required.
# ============================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="polymarket-bot"

echo "=== Installing Docker ==="
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker ec2-user

echo "=== Installing Docker Compose plugin ==="
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

echo "=== Setting up .env ==="
if [ ! -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
  echo ">>> Created .env from .env.example. Edit it before starting:"
  echo "    nano $REPO_DIR/.env"
fi

echo "=== Building Docker image ==="
cd "$REPO_DIR"
sudo docker compose build

echo "=== Creating systemd service ==="
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Polymarket Trading Bot
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit your .env:  nano $REPO_DIR/.env"
echo "  2. Start the bot:   sudo systemctl start $SERVICE_NAME"
echo "  3. View logs:       sudo docker compose logs -f"
echo "  4. Stop the bot:    sudo systemctl stop $SERVICE_NAME"
echo ""
echo "The bot will auto-start on reboot via systemd."
