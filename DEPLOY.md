# Деплой бота Period. на сервер

Инструкция для того, кто деплоит. Схема такая же, как у бота Тайного Санты (`~/zhabilizacia`): Docker, `restart: unless-stopped`, обновление через `bash install.sh`.

Код уже закоммичен на компьютере Полины в `C:\Users\PolinaOwner\Documents\cycle_bot`, ветка `main`. Секретов в репозитории нет: `.env` и база в `.gitignore`.

## 1. Создать приватный репозиторий на GitHub

1. Создай **приватный** репозиторий `Aionics/period_bot`, **пустой** (без README, .gitignore и лицензии).
2. **Settings → Deploy keys → Add deploy key**:
   - Title: `Polina PC`
   - Key:
     ```
     ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIN+epEInMTb4OAzSolAEIcS3jR6rhomUJwcXlHrknj1r period-bot deploy key
     ```
   - **Allow write access** — включить (с компьютера Полины будут пушить правки).
3. Скажи Полине, что готово, — код запушат с её компьютера.

Если репозиторий будет называться иначе, тоже скажи: поменяем адрес remote.

## 2. Доступ сервера к репозиторию

Deploy key на GitHub привязывается только к одному репозиторию, поэтому ключ от бота Санты сюда не подойдёт. Нужен отдельный ключ только на чтение:

```bash
ssh-keygen -t ed25519 -N "" -C "server period-bot" -f ~/.ssh/id_ed25519_period_server
cat ~/.ssh/id_ed25519_period_server.pub
```

Добавь его в тот же репозиторий: **Settings → Deploy keys → Add deploy key**, без write access.

```bash
GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_period_server -o IdentitiesOnly=yes" \
  git clone git@github.com:Aionics/period_bot.git ~/period-bot
cd ~/period-bot
git config core.sshCommand "ssh -i ~/.ssh/id_ed25519_period_server -o IdentitiesOnly=yes"
```

## 3. Перенести настройки и базу с компьютера Полины

Полина передаст два файла из `C:\Users\PolinaOwner\Documents\cycle_bot` (лично или в личном чате, **не в репозиторий**):

| Файл | Куда положить на сервере | Что внутри |
|---|---|---|
| `.env` | `~/period-bot/.env` | токен бота, ключ шифрования `CRYPT_KEY` |
| `data/cycle.db` | `~/period-bot/data/cycle.db` | зашифрованная база, пока только анкета Полины |

```bash
mkdir -p ~/period-bot/data && chmod 700 ~/period-bot/data
# скопировать файлы, например: scp .env data/cycle.db user@server:...
chmod 600 ~/period-bot/.env
```

- `.env` нужен обязательно: без него `install.sh` создаст **новый** `CRYPT_KEY`, и перенесённую базу будет не расшифровать.
- Базу переносить необязательно: без неё бот начнёт с чистой базы, и Полина пройдёт анкету заново (1 минута).
- `DB_PATH` из `.env` трогать не нужно: в контейнере его переопределяет `docker-compose.yml` (`/app/data/cycle.db`).

## 4. Запуск

> ⚠️ Перед запуском Полина должна **закрыть окно бота на компьютере** (`run.bat`). Две копии одного бота одновременно работать не могут — Telegram отдаёт обновления только одной.

```bash
cd ~/period-bot && bash install.sh
```

Скрипт увидит готовый `.env`, ничего не спросит, соберёт и запустит контейнер `period-bot` и выведет строку `Ссылка на бота: https://t.me/...`.

Если Docker ещё не в автозагрузке (для бота Санты уже должен быть): `sudo systemctl enable docker`.

## 5. Проверка

```bash
docker compose logs --tail 20
```

В логе должно быть `Run polling for bot @...`. После этого написать боту `/today` — он должен ответить.

## Обновление после правок в коде

Правки коммитятся и пушатся с компьютера Полины. На сервере:

```bash
cd ~/period-bot && bash install.sh
```

`.env` и база при обновлении не трогаются.

## Что важно знать

- **Сохрани копию `.env`** в надёжном месте (менеджер паролей и т.п.). Без `CRYPT_KEY` данные не расшифровать.
- **Резервных копий базы нет специально:** девушкам обещано, что после `/delete` их данных не останется нигде. Пожалуйста, не включай бэкапы `~/period-bot/data` в общие бэкапы сервера.
- В логах бота нет содержимого сообщений и дат: только служебные строки aiogram и типы ошибок.
- Девушкам честно написано, что человек с доступом к серверу технически может расшифровать базу. Просьба этого не делать.

| Что | Команда |
|---|---|
| Лог | `docker compose logs -f` |
| Перезапустить | `docker compose restart` |
| Остановить | `docker compose down` |
| Сколько пользователей (без имён) | команда `/count` в боте, если твой Telegram ID вписан в `ADMIN_IDS` в `.env` |
