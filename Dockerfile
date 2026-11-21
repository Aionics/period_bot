FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY bot.py cycle.py storage.py texts.py ./

# Зашифрованная база лежит в /app/data — эта папка подключается с диска сервера
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/cycle.db

CMD ["python", "bot.py"]
