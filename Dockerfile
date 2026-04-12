FROM python:3.12-slim

WORKDIR /app

# Зависимости ОС (для reportlab)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libfreetype6-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

# Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Приложение
COPY *.py ./
RUN mkdir -p /app/logs
COPY fonts/ ./fonts/

CMD ["python", "bot.py"]
