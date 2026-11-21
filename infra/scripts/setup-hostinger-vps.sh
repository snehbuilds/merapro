#!/usr/bin/env bash
set -euo pipefail

echo "=================================================="
echo "🚀 Setting up Mera Backend on Clean Ubuntu (Hostinger VPS)"
echo "=================================================="

# 1. System updates & prerequisites
echo "📦 Updating system packages..."
apt-get update && apt-get install -y curl git ufw debian-keyring debian-archive-keyring apt-transport-https

# 2. Install Docker
if ! command -v docker &> /dev/null; then
  echo "🐳 Installing Docker Engine..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
else
  echo "🐳 Docker is already installed."
fi

# 3. Install Caddy (lightweight reverse proxy with auto-HTTPS)
if ! command -v caddy &> /dev/null; then
  echo "🔒 Installing Caddy..."
  curl -1sLF 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLF 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
fi

# 4. Configure Firewall (UFW)
echo "🛡️ Configuring Firewall..."
ufw allow 22/tcp || true
ufw allow 80/tcp || true
ufw allow 443/tcp || true
ufw --force enable || true

# 5. Clone / Update Repository
APP_DIR="/opt/merapro"
if [ ! -d "$APP_DIR" ]; then
  echo "📂 Cloning repository to $APP_DIR..."
  git clone https://github.com/snehbuilds/merapro.git "$APP_DIR"
else
  echo "🔄 Pulling latest code..."
  cd "$APP_DIR" && git pull origin main
fi

cd "$APP_DIR"

# 6. Generate Master Key if needed
MERA_MASTER_KEY="oogh91mpkbwi46thf6rtag1p"

# 7. Write Production Environment File
echo "📝 Writing production .env..."
cat << 'EOF' > .env
MERA_ENV=production
MERA_SECRET_KEY=rZ3JpvQP8OoIZvKDYCFPGnZmHOk+Xe09aV7zVnMLHJz1/REK3kD8ABr8T9VNMQgD
MERA_MASTER_KEY=k2R3Rlh6S3pXZVZoMEF0SmZzM1VyY2VvM1dIdmF3eGk=
MERA_PUBLIC_URL=https://drtea.cloud
MERA_API_URL=http://api:8000
MERA_PUBLIC_API_URL=https://drtea.cloud
POSTGRES_PASSWORD=mera-prod-db-secret-2026
MERA_APP_DB_PASSWORD=mera-prod-app-secret-2026
DATABASE_URL=postgresql+asyncpg://mera_app:mera-prod-app-secret-2026@mera-db:5432/mera
DATABASE_MIGRATION_URL=postgresql+asyncpg://mera:mera-prod-db-secret-2026@mera-db:5432/mera
WEB_PROXY_SECRET=emujPEoZnCEqHNQ8As4u51KUVh0+sUMh+UN/5HjRvJs=
MERA_WEB_PROXY_SECRET=emujPEoZnCEqHNQ8As4u51KUVh0+sUMh+UN/5HjRvJs=
SANDBOX_MODE=false
SANDBOX_LLM=false
RATE_LIMIT_ENABLED=true
FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
MEDIA_ROOT=/var/lib/mera/media
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_MODE=webhook
EOF

# 8. Configure Caddy Reverse Proxy
echo "🌐 Configuring Caddy for api.drtea.cloud..."
cat << 'EOF' > /etc/caddy/Caddyfile
api.drtea.cloud {
    reverse_proxy 127.0.0.1:8000
}
EOF

systemctl restart caddy

# 9. Start Docker Compose Stack
echo "🚀 Building and starting Docker containers..."
docker compose -f docker-compose.backend.yml up -d --build

echo "=================================================="
echo "✅ Setup Complete!"
echo "Backend running on port 8000, proxied via Caddy on api.drtea.cloud"
echo "=================================================="
