FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY benefits_data.py benefits_bot.py storage.py ./
COPY data/ ./data/

CMD ["python", "benefits_bot.py"]
