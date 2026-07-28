#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустите от root: sudo bash scripts/install.sh"
  exit 1
fi

command -v openssl >/dev/null || { echo "Не найден openssl"; exit 1; }
command -v docker >/dev/null || {
  echo "Docker не найден. Установите Docker Engine и Compose plugin."
  exit 1
}

docker info >/dev/null 2>&1 || { echo "Docker daemon недоступен"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin не найден"; exit 1; }

cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  PANEL_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | head -c 24)"
  JWT_SECRET="$(openssl rand -hex 48)"
  cat > .env <<EOF
PANEL_USER=admin
PANEL_PASSWORD=${PANEL_PASSWORD}
JWT_SECRET=${JWT_SECRET}
PANEL_BIND=127.0.0.1
PANEL_PORT=8080
EOF
  chmod 600 .env
  echo "Создан .env"
  echo "Логин: admin"
  echo "Пароль: ${PANEL_PASSWORD}"
fi

docker compose up -d --build
echo
echo "DockPilot запущен: http://127.0.0.1:8080"
echo "Для внешнего доступа используйте Nginx/Caddy с HTTPS и не публикуйте Docker socket."
