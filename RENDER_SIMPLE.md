# CloudVape — простой деплой на Render (бесплатно)

Теперь всё работает **в одном сервисе**:
- API
- Telegram-бот
- + отдельная бесплатная Postgres

Нужно только **2 вещи** на бесплатном тарифе.

---

## Что создать на Render

### 1. Postgres
- New → Postgres
- Name: `cloudvape-db`
- Plan: Free

Скопируй **Internal Database URL**

### 2. Web Service (один)
- New → Web Service
- Репозиторий: твой GitHub
- Runtime: **Docker**
- Dockerfile Path: `backend/Dockerfile`
- Docker Context: `backend`

**Environment Variables:**

| Key              | Value                          |
|------------------|--------------------------------|
| `DATABASE_URL`   | Internal URL из Postgres       |
| `BOT_TOKEN`      | Токен от @BotFather            |
| `SECRET_KEY`     | любая длинная строка           |
| `OWNER_PASSWORD` | `admin123`                     |
| `DEBUG`          | `false`                        |

---

## После деплоя

- API: `https://твой-сервис.onrender.com`
- Документация: `https://твой-сервис.onrender.com/docs`
- Бот запускается автоматически в том же контейнере

Логин владельца: `owner` / пароль из `OWNER_PASSWORD`

---

Готово. Один Web Service + одна база = бесплатно и работает.


## Чтобы сайт не засыпал на бесплатном тарифе

Render Free засыпает через ~15 минут без запросов.

**Лучший бесплатный способ:**

1. Зайди на https://cron-job.org (бесплатно)
2. Создай задачу:
   - URL: `https://ТВОЙ-САЙТ.onrender.com/health`
   - Интервал: каждые 10 минут
3. Готово — сайт будет «тыкаться» снаружи и не уснёт

Дополнительно в Environment на Render можно указать:
`KEEP_ALIVE_URL` = `https://ТВОЙ-САЙТ.onrender.com`


## Вход продавца в 1 клик

В Environment на Render добавь:

```
WEB_BASE_URL=https://ТВОЙ-СЕРВИС.onrender.com
SECRET_KEY=<случайная строка 32+ символов>
OWNER_PASSWORD=<сложный пароль>
```

Без `WEB_BASE_URL` бот даст только цифровой код.
