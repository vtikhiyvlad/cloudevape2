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
