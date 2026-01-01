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
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),  # Логи в файл
        logging.StreamHandler()  # Логи в консоль
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Получение токена
API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')
if not API_TOKEN:
    raise ValueError("Токен Telegram не найден в .env!")


# Инициализация бота
bot = telebot.TeleBot(API_TOKEN)

# Словарь для хранения состояния диалога (user_id → stage, data)
user_state = {}

def format_tasks(tasks):
    """Форматировать список задач для вывода в Telegram"""
    if not tasks:
        return "У вас нет задач."
    lines = []
    for task in tasks:
        status = "[✅]" if task['done'] else "[✳️]"
        try:
            created_dt = datetime.fromisoformat(task['created_at'])
            created_str = created_dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, TypeError):
            created_str = str(task['created_at'])[:16]
        lines.append(f"{status} {task['task_id_in_list']}. {task['text']} ({created_str})")
    return "\n".join(lines)


@bot.message_handler(commands=['start'])
def start(message):
    logger.info(f"Получен /start от user_id={message.from_user.id}")
    bot.reply_to(message, "Привет! Я бот для управления задачами.\n"
                     "Чтобы добавить задачу:\n"
                     "— напишите любой текст, или\n"
                     "— используйте команду /add\n"
                         "/list — показать задачи\n"
                         "/done <номер> — отметить как выполненную\n"
                         "/delete <номер> — удалить задачу\n"
                         "/export — экспортировать задачи в CSV\n"
                         "/clear_all — удалить все задачи\n"
                         "/done_all — отметить все задачи как выполненные")


@bot.message_handler(commands=['list'])
def list_tasks(message):
    try:
        tasks = get_user_tasks(message.from_user.id)
        response = format_tasks(tasks)
        bot.reply_to(message, response)
    except Exception as e:
        logger.error(f"Ошибка при получении задач для user_id={message.from_user.id}: {e}")
        bot.reply_to(message, "Произошла ошибка при загрузке задач. Попробуйте позже.")


@bot.message_handler(commands=['done'])
def done_task(message):
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
    try:
        tasks = get_user_tasks(message.from_user.id)
        if not tasks:
            bot.reply_to(message, "У вас нет задач для экспорта.")
            return
        csv_data = "Номер;Статус;Текст;Дата создания\n"
        for task in tasks:
            status = "Выполнено" if task['done'] else "Не выполнено"
            try:
                created_dt = datetime.fromisoformat(task['created_at'])
                formatted_date = created_dt.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                formatted_date = str(task['created_at'])
            csv_data += f"{task['task_id_in_list']};{status};{task['text']};{formatted_date}\n"
        bot.send_document(
            message.chat.id,
            document=csv_data.encode('utf-8'),
            caption="Ваши задачи (CSV)"
        )
    except Exception as e:
        logger.error(f"Ошибка при экспорте задач user_id={message.from_user.id}: {e}")
        bot.reply_to(message, "Произошла ошибка при экспорте. Попробуйте позже.")


@bot.message_handler(func=lambda message: True, content_types=['text'])
def add_task(message):
    user_id = message.from_user.id
    text = message.text.strip()

    # Если идёт диалог — обрабатываем ответ
    if user_id in user_state:
        stage = user_state[user_id]['stage']
        data = user_state[user_id]['data']

        if stage == 'waiting_surname':
            data['surname'] = text
            user_state[user_id]['stage'] = 'waiting_task'
            bot.reply_to(message, "Спасибо! Теперь напишите саму задачу.")
            return

        elif stage == 'waiting_task':
            data['task_text'] = text

            # Сохраняем в БД
            task_id = save_task_to_db(
                user_id,
                data['task_text']
            )

            if task_id:
                bot.reply_to(
                    message,
                    f"[✳️] Задача №{task_id} добавлена!\n"
                    f"Задача: {data['task_text']}"
                )
            else:
                bot.reply_to(message, "Не удалось добавить задачу. Попробуйте ещё раз.")

            # Очищаем состояние
            user_state.pop(user_id, None)
            return

    # Если нет активного диалога — начинаем новый
    if text:  # проверяем, что сообщение не пустое
        user_state[user_id] = {
            'stage': 'waiting_surname',
            'data': {'task_text': text}
        }
        bot.reply_to(message, "Пожалуйста, укажите вашу фамилию:")
    else:
        bot.reply_to(message, "Текст задачи не может быть пустым!")



# Запуск бота
if __name__ == '__main__':
    try:
        logger.info("Запуск бота...")
        # Инициализируем БД при старте
        init_db()
        bot.polling(
            none_stop=True,
            interval=0,
            timeout=30
        )
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
