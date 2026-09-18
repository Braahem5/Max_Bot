FROM python:3.12-slim

# Логи сразу в stdout (видны в docker compose logs), без .pyc в образе.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY benefits_data.py benefits_bot.py ./
COPY data/ ./data/

# Бот не требует прав root: запускаем от обычного пользователя.
RUN useradd --create-home --uid 10001 bot
USER bot

CMD ["python", "benefits_bot.py"]
