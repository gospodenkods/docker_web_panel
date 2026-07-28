# DockPilot v1.0.6 — веб-панель управления Docker

Рабочий MVP панели для локального Docker Engine.

## Возможности

- обзор Docker Engine и сводные показатели;
- список контейнеров и состояния;
- создание контейнера из образа Docker Hub;
- старт, остановка, перезапуск, пауза, удаление;
- просмотр логов и CPU/RAM статистики;
- скачивание и удаление образов;
- создание и удаление сетей, IPAM subnet/gateway;
- быстрый запуск Nginx, Redis, PostgreSQL, MariaDB, Uptime Kuma;
- очистка неиспользуемых контейнеров, образов, сетей и томов;
- JWT-авторизация;
- адаптивный тёмный web-интерфейс.

## Требования

- Ubuntu 22.04/24.04 или актуальный Debian;
- Docker Engine и Docker Compose v2;
- запуск команд от `root` или пользователя с доступом к Docker.

## Быстрая установка на Ubuntu/Debian

Если Docker ещё не установлен:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git openssl
sudo systemctl enable --now docker
```

Скачайте DockPilot и запустите установщик:

```bash
sudo git clone https://github.com/gospodenkods/docker_web_panel.git /opt/dockpilot
cd /opt/dockpilot
sudo bash scripts/install.sh
```

Установщик создаст `/opt/dockpilot/.env` с правами `600`, сгенерирует
пароль администратора и JWT-секрет, соберёт образ и запустит панель.
Сохраните пароль, показанный установщиком.

По умолчанию DockPilot доступен только на сервере по адресу
`http://127.0.0.1:8080`.

### Доступ через SSH-туннель

На рабочем компьютере выполните:

```bash
ssh -L 8080:127.0.0.1:8080 root@SERVER_IP
```

После подключения откройте `http://127.0.0.1:8080`. Логин по умолчанию:
`admin`.

### Публичный HTTPS через Nginx

Готовый пример для `devops.dadway.ru` и HTTPS-порта `8090` находится в
[`deploy/nginx-devops.dadway.ru.conf`](deploy/nginx-devops.dadway.ru.conf).
Сам DockPilot при такой схеме остаётся на `127.0.0.1:8080`.

```bash
sudo apt install -y nginx certbot
sudo install -d -m 755 /var/www/letsencrypt
```

Сначала настройте HTTP virtual host на порту 80 с каталогом
`/var/www/letsencrypt` для ACME challenge, затем выпустите сертификат:

```bash
sudo certbot certonly --webroot \
  -w /var/www/letsencrypt \
  -d devops.dadway.ru \
  --agree-tos --register-unsafely-without-email --non-interactive
```

Установите готовую конфигурацию:

```bash
sudo cp deploy/nginx-devops.dadway.ru.conf /etc/nginx/sites-available/dockpilot
sudo ln -sfn /etc/nginx/sites-available/dockpilot /etc/nginx/sites-enabled/dockpilot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

За доверенным reverse proxy установите в `.env`:

```dotenv
TRUST_PROXY_HEADERS=true
```

Nginx должен перезаписывать `X-Forwarded-For` значением `$remote_addr`, как в
примере. Использование `$proxy_add_x_forwarded_for` позволит клиенту подменить
адрес, применяемый для ограничения попыток входа.

### Ручная установка

```bash
cp .env.example .env
chmod 600 .env
openssl rand -hex 24
openssl rand -hex 48
nano .env
sudo docker compose up -d --build
```

Первое случайное значение укажите как `PANEL_PASSWORD`, второе — как
`JWT_SECRET`.

## Nginx reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name docker.example.com;

    ssl_certificate /etc/letsencrypt/live/docker.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/docker.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

## Безопасность

Монтирование `/var/run/docker.sock` даёт приложению фактически полный контроль над Docker-хостом. Не публикуйте панель без HTTPS, сильного пароля, firewall/VPN и ограничения доступа по IP. Для усиления защиты можно использовать отдельный Docker Socket Proxy, rootless Docker или TLS-защищённый удалённый Docker API.

## Структура

- `app/main.py` — FastAPI backend и Docker SDK;
- `app/static/` — web-интерфейс;
- `docker-compose.yml` — запуск панели;
- `scripts/install.sh` — автоматическая установка.

## Проверка

```bash
curl -fsS http://127.0.0.1:8080/api/health
sudo docker compose ps
sudo docker compose logs --tail=100
```

## Самопроверка

```bash
bash scripts/self-check.sh
```

Скрипт проверяет структуру, синтаксис Python/JavaScript/Shell, unit-тесты и, при наличии Docker Engine, выполняет `docker compose config` и сборку образа.
Docker-проверка выполняется в отдельном временном Compose-проекте со случайным
локальным портом и не останавливает рабочий экземпляр DockPilot. После проверки
удаляются только созданные self-check контейнеры, сеть, том и локальный образ.

Для проверки без пропусков:

```bash
STRICT=1 bash scripts/self-check.sh
```

## Обновление

```bash
cd /opt/dockpilot
sudo git pull --ff-only
sudo docker compose up -d --build
curl -fsS http://127.0.0.1:8080/api/health
```

Файл `.env` и том `dockpilot_data` при обновлении сохраняются.

## Остановка и удаление

Остановить панель, сохранив данные:

```bash
cd /opt/dockpilot
sudo docker compose down
```

Удалить панель вместе с её именованным томом:

```bash
cd /opt/dockpilot
sudo docker compose down -v
```

Команда с `-v` необратимо удаляет данные тома DockPilot.


## Исправления 1.0.6

- ограничение частоты неудачных попыток авторизации: по умолчанию 5 попыток за 5 минут на IP;
- Redis в быстром шаблоне запускается с автоматически созданным паролем;
- входное значение имени образа валидируется отдельной Pydantic-моделью;
- исправлена маркировка версий в документации;
- служебные `__pycache__` и `.pyc` удаляются из поставки;
- сохранены исправления безопасной публикации портов на `127.0.0.1`, валидации имён и проверки Docker runtime.

Параметры ограничения входа можно изменить переменными `LOGIN_MAX_ATTEMPTS` и `LOGIN_WINDOW_SECONDS`. Заголовок `X-Forwarded-For` по умолчанию не учитывается. Включайте `TRUST_PROXY_HEADERS=true` только за доверенным reverse proxy и не разрешайте клиентам обращаться к панели в обход proxy.
