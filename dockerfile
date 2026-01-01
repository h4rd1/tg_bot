# 1. Базовый образ (Python 3.11, облегчённая версия)
FROM python:3.11-slim


# 2. Метаданные (опционально)
LABEL description="Telegram bot with PostgreSQL + Redis integration"


# 3. Установка рабочей директории внутри контейнера
WORKDIR /app


# 4. Копирование файла зависимостей
COPY requirements.txt .

# Установка зависимостей без кэширования (экономим место)
RUN pip install --no-cache-dir -r requirements.txt


# 5. Копирование кода приложения
COPY db_connect.py .
COPY bot_functions.py .

# Если есть другие файлы (например, .env шаблон), копируем их тоже
# COPY .env .

# COPY other_file.py .


# 6. Открытие портов (если бот использует вебхуки, а не polling)
# EXPOSE 8443


# 7. Команда запуска
CMD ["python", "bot_functions.py"]
