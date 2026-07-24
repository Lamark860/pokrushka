# Развёртывание на сервере

Репозиторий публичный, поэтому адрес сервера, пользователь и любые ключи здесь не пишутся:
вместо них `<VPS_IP>`, `<VPS_USER>`. Настоящие значения — в приватной документации проекта
и в секретах GitHub.

## Что где лежит

| Что | Где |
|---|---|
| Код | `/opt/projects/pokrushka` (клон этого репозитория, ветка `main`) |
| Секреты | `/opt/projects/pokrushka/.env.prod` — только на сервере, в git не попадает |
| База | том Docker `pokrushka_traff-pgdata`, наружу порт не открыт |
| Веб | порт `8094` на сервере (80 и 443 заняты общим Traefik) |

Сервер общий: рядом живут другие проекты со своими контейнерами и томами. Все команды
ниже работают только внутри каталога проекта и трогают только его контейнеры.

## Первая установка

```bash
ssh <VPS_USER>@<VPS_IP>

sudo mkdir -p /opt/projects/pokrushka
sudo chown $USER:$USER /opt/projects/pokrushka
git clone https://github.com/Lamark860/pokrushka.git /opt/projects/pokrushka
cd /opt/projects/pokrushka

cp .env.prod.example .env.prod
# сгенерировать пароль БД и ключ подписи кук
openssl rand -base64 32   # → POSTGRES_PASSWORD
openssl rand -base64 48   # → SECRET_KEY
nano .env.prod            # заполнить, вписать ROUTER_API_KEY и PUBLIC_BASE_URL
chmod 600 .env.prod

docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

Создать первого пользователя (логин и пароль берутся из `BOOTSTRAP_*` в `.env.prod`):

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod \
  run --rm web python -m app.cli bootstrap
```

Команда идемпотентна: если пользователь уже есть, она ничего не меняет. После неё пароль
из `.env.prod` можно стереть — он больше не нужен, меняется командой
`... run --rm web python -m app.cli passwd <email>`.

Проверка: `curl -s -o /dev/null -w '%{http_code}' http://localhost:8094/healthz` → `200`,
снаружи — `http://<VPS_IP>:8094/`.

## Обновления

Пуш в `main` запускает GitHub Actions `.github/workflows/deploy.yml`: он заходит на сервер,
делает `git reset --hard origin/main`, пересобирает `web` и ждёт ответа `/healthz`.
Бот и планировщик поднимаются только если в `.env.prod` заполнены их ключи.

Секреты репозитория (Settings → Secrets and variables → Actions):

| Секрет | Значение |
|---|---|
| `VPS_HOST` | адрес сервера |
| `VPS_USER` | пользователь на сервере |
| `VPS_PATH` | `/opt/projects/pokrushka` |
| `VPS_SSH_KEY` | приватный ключ деплоя целиком, включая строки `BEGIN`/`END` |

Руками то же самое:

```bash
cd /opt/projects/pokrushka
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build web
```

Миграции прогоняются отдельным одноразовым контейнером `migrate` до старта веба — вручную
`alembic upgrade head` вызывать не нужно.

## Трекинг подписок

Бот и планировщик выключены, пока не выданы токен бота и ключи TGTrack. Когда появятся:

```bash
nano .env.prod   # TELEGRAM_BOT_TOKEN, TGTRACK_TG_API_KEY, TGTRACK_MAX_API_KEY, NOTIFY_CHAT_ID
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile tracking up -d bot scheduler
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm web python -m app.cli check-bot
```

`check-bot` проверяет, жив ли токен и видит ли бот канал: он должен быть администратором
канала с правом приглашать пользователей.

## Домен

Пока сервис живёт на голом IP с портом. Это терпимо для внутренней панели, но плохо для
ссылок `/r/<code>` в опубликованных статьях: читатель Дзена видит `http://<IP>:8094/...`.
Поэтому до появления домена счётчик кликов у проекта лучше держать выключенным — тогда в
статьи идут прямые ссылки на канал.

Когда домен появится, сервис уходит за общий Traefik: убрать из `web` секцию `ports`,
добавить сеть `proxy` и метки маршрутизации (как у соседних проектов на этом сервере),
поправить `PUBLIC_BASE_URL` на `https://<домен>` и включить счётчик кликов.

## Обслуживание

```bash
# логи
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f web

# состояние
docker compose -f docker-compose.prod.yml --env-file .env.prod ps

# бэкап базы
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db \
  pg_dump -U traff traff | gzip > ~/backups/pokrushka-$(date +%F).sql.gz

# восстановление
gunzip -c ~/backups/pokrushka-2026-07-24.sql.gz | \
  docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T db psql -U traff traff
```

Регулярный бэкап и ротация логов пока не настроены — это остаток пятого этапа.
