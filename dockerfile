# Базовый образ с Python 3.11
FROM python:3.11-slim


# Рабочий каталог в контейнере
WORKDIR /app


# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Копируем код бота
COPY . .


# Порт, который будет слушать бот (если используется вебхук)
# EXPOSE 8443


# Запуск бота
CMD ["python", "bot.py"]
