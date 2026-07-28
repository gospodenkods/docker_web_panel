#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
STRICT="${STRICT:-0}"
skipped=0

echo '[1/6] Проверка файлов'
for f in Dockerfile docker-compose.yml requirements.txt app/main.py app/static/index.html app/static/app.js app/static/style.css; do
  test -s "$f" || { echo "Отсутствует $f"; exit 1; }
done

echo '[2/6] Python syntax'
python3 -m py_compile app/main.py tests/test_core.py

echo '[3/6] JavaScript syntax'
if command -v node >/dev/null; then node --check app/static/app.js; else echo 'SKIP: node не установлен'; skipped=$((skipped+1)); fi

echo '[4/6] Shell syntax'
bash -n scripts/install.sh scripts/self-check.sh

echo '[5/6] Python unit tests'
if python3 -c 'import fastapi,docker,jose,passlib,pydantic' 2>/dev/null; then
  PANEL_USER=admin PANEL_PASSWORD=test-password JWT_SECRET=test-secret-at-least-long-enough \
    python3 -m unittest -v tests/test_core.py
else
  echo 'SKIP: Python-зависимости не установлены. Выполните: pip install -r requirements.txt'
  skipped=$((skipped+1))
fi

echo '[6/6] Docker compose/build/runtime'
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  project="dockpilot_selfcheck_$$"
  container_name="dockpilot-self-check-$$"
  env_file="$(mktemp)"
  override_file="$(mktemp)"
  cat > "$env_file" <<EOF
PANEL_USER=admin
PANEL_PASSWORD=$(openssl rand -hex 18)
JWT_SECRET=$(openssl rand -hex 48)
PANEL_BIND=127.0.0.1
PANEL_PORT=0
EOF
  cat > "$override_file" <<EOF
services:
  dockpilot:
    container_name: ${container_name}
EOF
  chmod 600 "$env_file"
  compose=(docker compose -f docker-compose.yml -f "$override_file" --env-file "$env_file" -p "$project")
  cleanup() {
    "${compose[@]}" down --rmi local -v --remove-orphans >/dev/null 2>&1 || true
    rm -f "$env_file" "$override_file"
  }
  trap cleanup EXIT
  "${compose[@]}" config >/dev/null
  "${compose[@]}" build
  "${compose[@]}" up -d
  published_port="$("${compose[@]}" port dockpilot 8080 | awk -F: 'END{print $NF}')"
  test -n "$published_port" || { echo 'Не удалось определить опубликованный порт'; exit 1; }
  for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${published_port}/api/health" >/dev/null; then break; fi
    sleep 1
  done
  curl -fsS "http://127.0.0.1:${published_port}/api/health" >/dev/null
  echo 'Docker runtime smoke test: OK'
else
  echo 'SKIP: Docker Engine недоступен'
  skipped=$((skipped+1))
fi

if (( skipped > 0 )); then
  echo "Self-check завершён с пропусками: $skipped"
  if [[ "$STRICT" == "1" ]]; then exit 2; fi
else
  echo 'Self-check полностью пройден.'
fi
