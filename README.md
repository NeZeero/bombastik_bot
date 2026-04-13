# 💅 Beauty Bot — Telegram бот для записи клиентов

## Быстрый старт

### 1. Установка зависимостей
```bash
pip install aiogram==3.7.0 apscheduler==3.10.4
```

### 2. Настройка (файл config.py)

Откройте `config.py` и заполните:
- `BOT_TOKEN` — токен от @BotFather
- `MASTER_IDS` — ваш Telegram ID или несколько ID мастеров
- `PORTFOLIO_TEXT` и `ADDRESS_TEXT` — свои тексты

### 3. Запуск
```bash
python bot.py
```

---

## Структура проекта

```
beauty_bot/
├── bot.py           # Точка входа
├── config.py        # Настройки (токен, ID мастера, тексты)
├── database.py      # База данных SQLite
├── reminders.py     # Напоминания клиентам
├── requirements.txt
└── handlers/
    ├── client_v2.py # Активная логика записи клиента
    └── master.py    # Панель управления мастера
```

---

## Возможности

### Для клиентов
- Главное меню: Запись / Портфолио / Наш адрес
- Выбор даты → выбор времени (🟢 свободно / ❌ занято)
- Ввод имени и номера (или поделиться контактом из Telegram)
- Напоминание за 4 часа до визита

### Для мастера (команда /master)
- Добавить рабочие дни и часы на 30 дней вперёд
- Удалить незанятые слоты
- Просмотр всех записей с именем и телефоном
- Уведомление о каждой новой записи

---

## Деплой на сервер

**Systemd (Linux VPS):**
Создайте `/etc/systemd/system/beauty_bot.service`:
```ini
[Unit]
Description=Beauty Telegram Bot
[Service]
ExecStart=/usr/bin/python3 /path/to/beauty_bot/bot.py
WorkingDirectory=/path/to/beauty_bot
Restart=always
[Install]
WantedBy=multi-user.target
```
Затем: `sudo systemctl enable beauty_bot && sudo systemctl start beauty_bot`

**Railway / Render:** загрузите папку, команда старта: `python bot.py`

---

## FAQ

**Как узнать свой Telegram ID?** Напишите @userinfobot.

**Где хранятся данные?** В файле `beauty_bot.db` (SQLite) рядом с ботом.
