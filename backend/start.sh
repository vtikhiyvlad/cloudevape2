#!/bin/bash
set -e

echo "🚀 Запуск CloudVape..."

# Запускаем бота в фоне (если есть токен)
if [ -n "$BOT_TOKEN" ] && [ "$BOT_TOKEN" != "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz" ]; then
    echo "🤖 Запускаем Telegram-бота..."
    python -m app.bot.main &
    BOT_PID=$!
    echo "Бот запущен (PID $BOT_PID)"
else
    echo "⚠️ BOT_TOKEN не указан — бот не запускается"
fi

# Запускаем API (главный процесс)
echo "🌐 Запускаем API..."
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
