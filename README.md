# Revenue Opportunity Scanner 💰🔍

Telegram-бот, который анализирует 6 месяцев рабочих переписок через Claude AI и находит упущенные возможности для заработка.

## Что делает

1. **Сканирует** рабочие чаты в Telegram (Telethon) за последние 6 месяцев
2. **Анализирует** переписки через Claude API — находит незакрытые сделки, потенциальных клиентов, партнёрства
3. **Строит профиль** — понимает стиль коммуникации, энергию, слепые зоны
4. **Генерирует план** — конкретные действия с привязкой к деньгам
5. **Трекает** — отмечай done/skip, следи за pipeline

## Команды бота

| Команда | Что делает |
|---------|-----------|
| `/scan` | Полный скан переписок за 6 мес |
| `/plan` | План действий на сегодня |
| `/pipeline` | Активные возможности |
| `/opp <id>` | Подробности по возможности |
| `/done <id>` | Отметить выполненной |
| `/skip <id>` | Пропустить |
| `/start <id>` | В работе |
| `/stats` | Статистика дохода |
| `/projects` | По проектам |
| `/profile` | Психологический профиль |

## Деплой на Railway

### 1. Создай проект
```bash
# GitHub
cd revenue-scanner
git init
git add .
git commit -m "Revenue Opportunity Scanner"
gh repo create CryptoKong999/revenue-scanner --private --push
```

### 2. Railway
- New Project → Deploy from GitHub → revenue-scanner
- Add PostgreSQL plugin

### 3. Environment Variables

```
TELEGRAM_BOT_TOKEN=         # Новый бот через @BotFather
TELEGRAM_OWNER_ID=271065518 # Твой Telegram ID
TELEGRAM_API_ID=            # Из my.telegram.org  
TELEGRAM_API_HASH=          # Из my.telegram.org
TELEGRAM_STRING_SESSION=    # Существующая StringSession
ANTHROPIC_API_KEY=          # Claude API key
DATABASE_URL=               # Автоматически от Railway PostgreSQL

# Опционально:
WORK_CHAT_IDS=              # Конкретные ID чатов через запятую (если пусто — авто-детект)
SCAN_MONTHS=6               # Сколько месяцев сканировать
MAX_MESSAGES_PER_CHAT=2000  # Лимит сообщений на чат
```

### 4. Запуск
Railway автоматически задеплоит при пуше. После деплоя:
1. Открой бота в Telegram
2. `/scan` — запусти первый скан (3-10 минут)
3. `/plan` — получи первый план действий

## Архитектура

```
Telegram Chats (6 мес.)
    ↓ Telethon
Chat Scanner
    ↓ Messages
Claude API Analyzer
    ↓ Opportunities + Profile
PostgreSQL
    ↓ 
Telegram Bot Interface
    ↓ done/skip/plan
Revenue Tracking
```

## Настройка чатов для скана

**Вариант A: Автодетект** (по умолчанию)
Бот сканирует все диалоги и группы, ищет по ключевым словам (zbs, реклама, клиент, проект, съёмка и т.д.)

**Вариант B: Явный список**
Укажи `WORK_CHAT_IDS` — ID чатов через запятую. Можно юзернеймы (@channel) или числовые ID.

## Стоимость

- Claude API: ~$0.50-2.00 за полный скан (зависит от объёма переписок)
- Ежедневный план: ~$0.02-0.05
- Railway: зависит от плана
