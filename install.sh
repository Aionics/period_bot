#!/usr/bin/env bash
# Установка и обновление бота Period. на сервере.
#
# Первый запуск:   REPO_URL=https://github.com/<owner>/<repo>.git bash install.sh
# Обновление:      cd ~/period-bot && bash install.sh
#
# Переменные (необязательно):
#   REPO_URL   — адрес репозитория (нужен только при первой установке)
#   APP_DIR    — куда ставить (по умолчанию ~/period-bot)
#   BRANCH     — ветка (по умолчанию main)
#   BOT_TOKEN, ADMIN_IDS — чтобы не вводить их вручную при первой установке
set -euo pipefail

REPO_URL="${REPO_URL:-}"
APP_DIR="${APP_DIR:-$HOME/period-bot}"
BRANCH="${BRANCH:-main}"

say()  { printf '\n> %s\n' "$*"; }
fail() { printf '\n❌ %s\n' "$*" >&2; exit 1; }

command -v git    >/dev/null || fail "Не найден git"
command -v docker >/dev/null || fail "Не найден docker"
docker compose version >/dev/null 2>&1 || fail "Не найден docker compose (нужна версия 2: «docker compose»)"
docker info >/dev/null 2>&1 || fail "Нет доступа к docker. Добавьте пользователя в группу docker или запустите через sudo"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/.git" ] && [ -f "$SCRIPT_DIR/bot.py" ]; then
    APP_DIR="$SCRIPT_DIR"
fi

if [ -d "$APP_DIR/.git" ]; then
    say "Обновляю код в $APP_DIR"
    git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
    [ -n "$REPO_URL" ] || fail "Укажите адрес репозитория: REPO_URL=https://github.com/<owner>/<repo>.git bash install.sh"
    say "Скачиваю код в $APP_DIR"
    git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
mkdir -p data
chmod 700 data

ask() {  # ask <переменная> <вопрос>
    local var="$1" prompt="$2" value="${!1:-}"
    if [ -z "$value" ]; then
        [ -r /dev/tty ] || fail "Задайте $var через переменную окружения"
        read -rp "$prompt" value </dev/tty
    fi
    printf '%s' "$value"
}

if [ ! -f .env ]; then
    say "Первая настройка"
    token="$(ask BOT_TOKEN 'Токен бота от @BotFather: ')"
    [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] || fail "Токен выглядит неправильно"
    admins="${ADMIN_IDS:-}"
    [ -z "$admins" ] && [ -r /dev/tty ] && read -rp 'Ваш Telegram ID для команды /count (можно пропустить, Enter): ' admins </dev/tty
    [[ -z "$admins" || "$admins" =~ ^[0-9]+(,[0-9]+)*$ ]] || fail "ID должны быть числами через запятую"
    # Ключ шифрования (32 случайных байта)
    key="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')"
    umask 077
    cat > .env <<ENV
BOT_TOKEN=$token
CRYPT_KEY=$key
INVITE_CODE=
ADMIN_IDS=$admins
DB_PATH=/app/data/cycle.db
ENV
    chmod 600 .env
    say "Настройки сохранены в $APP_DIR/.env"
    say "⚠️  Сохраните копию .env в надёжном месте: без CRYPT_KEY данные не расшифровать."
else
    say "Настройки уже есть ($APP_DIR/.env) — оставляю как есть"
fi

say "Собираю и запускаю контейнер"
docker compose up -d --build --remove-orphans

if command -v systemctl >/dev/null && ! systemctl is-enabled docker >/dev/null 2>&1; then
    say "⚠️  Docker не стоит в автозагрузке. Чтобы бот поднимался после отключения света:"
    echo "    sudo systemctl enable docker"
fi

say "Жду подключения к Telegram…"
for _ in $(seq 1 20); do
    if docker compose logs bot 2>/dev/null | grep -q "Run polling for bot"; then
        name="$(docker compose logs bot | grep -o 'Run polling for bot @[A-Za-z0-9_]*' | tail -1 | sed 's/.*@//')"
        code="$(grep '^INVITE_CODE=' .env | cut -d= -f2)"
        say "Готово! Бот работает."
        [ -n "$name" ] && echo "    Ссылка на бота: https://t.me/$name${code:+?start=$code}"
        echo "    Лог: cd $APP_DIR && docker compose logs -f"
        exit 0
    fi
    sleep 1
done
docker compose logs --tail 30 bot
fail "Бот не подключился за 20 секунд — посмотрите лог выше"
