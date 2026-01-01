import os
import logging
import time
import csv
from datetime import datetime
from dotenv import load_dotenv
import telebot
import json

from db_connect import (
    init_db, get_user_tasks, save_task_to_db, mark_done_in_db,
    delete_task_from_db, clear_all_tasks_db, done_all_tasks_db
)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Получение токена
API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
if not API_TOKEN:
    raise ValueError("Токен Telegram не найден в .env!")

# Инициализация бота
bot = telebot.TeleBot(API_TOKEN)


def format_tasks(tasks):
    """Форматировать список задач для вывода в Telegram"""
    if not tasks:
        return "У вас нет задач."
    lines = []
    for task in tasks:
        status = "[✅]" if task['done'] else "[✳️]"
        # Форматируем дату: берём первые 19 символов или преобразуем ISO
        try:
            created_dt = datetime.fromisoformat(task['created_at'])
            created_str = created_dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            created_str = str(task['created_at'])[:16]  # обрезка строки
        lines.append(f"{status} {task['task_id_in_list']}. {task['text']} ({created_str})")
    return "\n".join(lines)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Привет! Я бот для управления задачами.\n"
                         "Все сообщения автоматически становятся задачами.\n"
                         "Команды:\n"
                         "/list — показать задачи\n"
                         "/done <номер> — отметить как выполненную\n"
                         "/delete <номер> — удалить задачу\n"
                         "/export — экспортировать задачи в CSV\n"
                         "/clear_all — удалить все задачи\n"
                         "/done_all — отметить все задачи как выполненные")

@bot.message_handler(commands=['list'])
def list_tasks(message):
    """Показать список задач пользователя"""
    try:
        tasks = get_user_tasks(message.from_user.id)
        response = format_tasks(tasks)
        bot.reply_to(message, response)
    except Exception as e:
        logger.error(f"Ошибка при получении задач для user_id={message.from_user.id}: {e}")
        bot.reply_to(message, "Произошла ошибка при загрузке задач. Попробуйте позже.")

@bot.message_handler(commands=['done'])
def done_task(message):
    """Отметить задачу как выполненную"""
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Используйте: /done <номер_задачи>")
        return

    try:
        task_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "Номер задачи должен быть числом!")
        return

    try:
        if mark_done_in_db(message.from_user.id, task_id):
            bot.reply_to(message, f"[✅] Задача №{task_id} отмечена как выполненная!")
        else:
            bot.reply_to(message, "Задача не найдена или уже выполнена.")
    except Exception as e:
        logger.error(f"Ошибка при отметке задачи user_id={message.from_user.id}, task_id={task_id}: {e}")
        bot.reply_to(message, "Произошла ошибка. Попробуйте снова.")


@bot.message_handler(commands=['delete'])
def delete_task(message):
    """Удалить задачу"""
    args = message.text.split()
    if len(args) != 2:
        bot.reply_to(message, "Используйте: /delete <номер_задачи>")
        return

    try:
        task_id = int(args[1])
    except ValueError:
        bot.reply_to(message, "Номер задачи должен быть числом!")
        return

    try:
        if delete_task_from_db(message.from_user.id, task_id):
            bot.reply_to(message, f"[❌] Задача №{task_id} удалена!")
        else:
            bot.reply_to(message, "Задача не найдена.")
    except Exception as e:
        logger.error(f"Ошибка при удалении задачи user_id={message.from_user.id}, task_id={task_id}: {e}")
        bot.reply_to(message, "Произошла ошибка. Попробуйте снова.")


@bot.message_handler(commands=['clear_all'])
def clear_all_tasks(message):
    """Удалить все задачи пользователя"""
    user_id = message.from_user.id

    try:
        success, deleted_count = clear_all_tasks_db(user_id)
        if success:
            if deleted_count > 0:
                bot.reply_to(message, f"[❌] Удалено {deleted_count} задач!")
            else:
                bot.reply_to(message, "У вас нет задач для удаления.")
        else:
            bot.reply_to(message, "Не удалось удалить задачи. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка при очистке всех задач user_id={user_id}: {e}")
        bot.reply_to(message, "Произошла ошибка. Попробуйте снова.")

@bot.message_handler(commands=['done_all'])
def done_all_tasks(message):
    """Отметить все задачи как выполненные"""
    user_id = message.from_user.id

    try:
        success, updated_count = done_all_tasks_db(user_id)
        if success:
            if updated_count > 0:
                bot.reply_to(message, f"[✅] Отмечено как выполненные: {updated_count} задач!")
            else:
                bot.reply_to(message, "Нет задач для отметки как выполненных.")
        else:
            bot.reply_to(message, "Не удалось отметить задачи. Попробуйте позже.")
    except Exception as e:
        logger.error(f"Ошибка при отметке всех задач как выполненных user_id={user_id}: {e}")
        bot.reply_to(message, "Произошла ошибка. Попробуйте снова.")

@bot.message_handler(commands=['export'])
def export_tasks(message):
    """Экспортировать задачи в CSV"""
    try:
        tasks = get_user_tasks(message.from_user.id)
        if not tasks:
            bot.reply_to(message, "У вас нет задач для экспорта.")
            return

        # Создаём CSV
        csv_data = "Номер;Статус;Текст;Дата создания\n"
        for task in tasks:
            status = "Выполнено" if task['done'] else "Не выполнено"
            try:
                created_dt = datetime.fromisoformat(task['created_at'])
                formatted_date = created_dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                formatted_date = str(task['created_at'])
            csv_data += f"{task['task_id_in_list']};{status};{task['text']};{formatted_date}\n"

        # Отправляем файл
        bot.send_document(
            message.chat.id,
            document=csv_data.encode('utf-8'),
            caption="Ваши задачи (CSV)"
        )
    except Exception as e:
        logger.error(f"Ошибка при экспорте задач user_id={message.from_user.id}: {e}")
        bot.reply_to(message, "Произошла ошибка при экспорте. Попробуйте позже.")

# Обработчик всех текстовых сообщений (добавление новой задачи)
@bot.message_handler(func=lambda message: True, content_types=['text'])
def add_task(message):
    """Добавить новую задачу из текста сообщения"""
    user_id = message.from_user.id
    text = message.text.strip()

    if not text:
        bot.reply_to(message, "Текст задачи не может быть пустым!")
        return

    task_id = save_task_to_db(user_id, text)
    if task_id:
        bot.reply_to(message, f"[✳️] Задача №{task_id} добавлена!")
    else:
        bot.reply_to(message, "Не удалось добавить задачу. Попробуйте ещё раз.")

if __name__ == '__main__':
    logger.info("Бот запущен. Ожидание сообщений...")
    init_db()

    while True:
        try:
            bot.polling(non_stop=True, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка в polling: {e}")
            logger.info("Перезапуск polling через 10 секунд...")
            time.sleep(10)
        else:
            logger.info("Polling завершён. Перезапуск через 5 секунд...")
            time.sleep(5)

