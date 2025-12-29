import os
import json
import time
import logging
import sys
import csv
from datetime import datetime
from dotenv import load_dotenv
import telebot

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Получение токена из переменных окружения
API_TOKEN = os.getenv('TELEGRAM_API_TOKEN')

# Проверка наличия токена
if not API_TOKEN:
    logger.error("Токен API не найден в переменных окружения!")
    logger.info("Доступные переменные окружения (частично):")
    for key in os.environ:
        if 'TOKEN' in key or 'BOT' in key:
            logger.info(f"{key}: {os.environ[key]}")
    raise ValueError("Токен бота не найден! Проверьте файл .env или переменные окружения.")


# Инициализация бота
bot = telebot.TeleBot(API_TOKEN)

# Файлы для хранения данных
TASKS_FILE = 'tasks.json'
CSV_FILE = 'tasks_export.csv'

def load_tasks():
    """Загрузить задачи из файла"""
    if os.path.exists(TASKS_FILE):
        try:
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.debug(f"Задачи загружены из {TASKS_FILE}")
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка чтения JSON из {TASKS_FILE}: {e}")
            return {}
        except Exception as e:
            logger.error(f"Неожиданная ошибка при чтении {TASKS_FILE}: {e}")
            return {}
    logger.info(f"Файл {TASKS_FILE} не найден. Создан пустой словарь задач.")
    return {}

def save_tasks(tasks):
    """Сохранить задачи в файл"""
    try:
        with open(TASKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
        logger.debug(f"Задачи сохранены в {TASKS_FILE}")
        return True
    except IOError as e:
        logger.error(f"Ошибка сохранения задач в {TASKS_FILE}: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при сохранении задач: {e}")
    return False

def export_to_csv(user_id):
    """Экспортировать задачи пользователя в CSV-файл"""
    tasks = get_user_tasks(user_id)
    
    try:
        file_path = os.path.abspath(CSV_FILE)
        logger.debug(f"Экспорт в CSV: {file_path} (пользователь {user_id}, {len(tasks)} задач)")


        with open(CSV_FILE, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['id', 'text', 'done', 'created_at']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            if tasks:
                for task in tasks:
                    writer.writerow({
                        'id': task['id'],
                        'text': task['text'],
                        'done': 'Да' if task['done'] else 'Нет',
                        'created_at': task['created_at']
                    })
        
        logger.info(f"Задачи пользователя {user_id} экспортированы в {file_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при экспорте в CSV ({CSV_FILE}): {e}")
        return False

def get_user_tasks(user_id):
    """Получить задачи пользователя"""
    tasks = load_tasks()
    return tasks.get(str(user_id), [])


def add_task(user_id, text):
    """Добавить задачу"""
    tasks = load_tasks()
    user_id_str = str(user_id)
    
    # Создаём список задач для пользователя, если его нет
    if user_id_str not in tasks:
        tasks[user_id_str] = []
    
    
    user_tasks = tasks[user_id_str]
    new_task = {
        'id': len(user_tasks) + 1,
        'text': text,
        'done': False,
        'created_at': datetime.now().isoformat()
    }
    user_tasks.append(new_task)
    tasks[user_id_str] = user_tasks
    
    
    if save_tasks(tasks) and export_to_csv(user_id):
        return new_task['id']
    return None

def mark_done(user_id, task_id):
    """Отметить задачу как выполненную"""
    tasks = load_tasks()
    user_id_str = str(user_id)
    user_tasks = tasks.get(user_id_str, [])
    
    
    for task in user_tasks:
        if task['id'] == task_id:
            task['done'] = True
            tasks[user_id_str] = user_tasks
            
            if save_tasks(tasks) and export_to_csv(user_id):
                return True
            return False
    return False  # Задача не найдена

def delete_task(user_id, task_id):
    """Удалить задачу"""
    tasks = load_tasks()
    user_id_str = str(user_id)
    user_tasks = tasks.get(user_id_str, [])
    
    
    # Ищем задачу по ID
    task_index = -1
    for i, task in enumerate(user_tasks):
        if task['id'] == task_id:
            task_index = i
            break
    
    if task_index == -1:
        return False  # Задача не найдена
    
    # Удаляем задачу
    del user_tasks[task_index]
    
    # Пересчитываем ID оставшихся задач
    for idx, task in enumerate(user_tasks, 1):
        task['id'] = idx
    # Обновляем словарь задач
    tasks[user_id_str] = user_tasks
    
    # Сохраняем изменения
    if save_tasks(tasks):
        # Экспортируем в CSV
        if export_to_csv(user_id):
            return True
        else:
            logger.warning(f"Не удалось обновить CSV после удаления задачи №{task_id} для пользователя {user_id}")
            return False
    else:
        logger.error(f"Не удалось сохранить изменения в {TASKS_FILE} после удаления задачи №{task_id}")
        return False

def format_tasks(tasks):
    """Форматировать задачи для вывода"""
    if not tasks:
        return "У вас нет задач."
    lines = []
    for task in tasks:
        status = "[✓]" if task['done'] else "[⬜]"
        lines.append(f"{status} {task['id']}. {task['text']}")
    return "\n".join(lines)

# Обработчики команд
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "Привет! Я бот для управления задачами.\n"
                         "Используйте:\n"
                         "/add <текст> — добавить задачу\n"
                         "/list — показать задачи\n"
                         "/done <номер> — отметить как выполненную\n"
                         "/delete <номер> — удалить задачу\n"
                         "/export — экспортировать задачи в CSV")

@bot.message_handler(commands=['add'])
def add(message):
    text = message.text[len('/add '):].strip()
    if not text:
        bot.reply_to(message, "[!] Укажите текст задачи после /add")
        return
    task_id = add_task(message.from_user.id, text)
    if task_id:
        bot.reply_to(message, f"[✓] Задача №{task_id} добавлена!")
    else:
        bot.reply_to(message, "[!] Не удалось добавить задачу. Попробуйте ещё раз.")

@bot.message_handler(commands=['list'])
def list_tasks(message):
    tasks = get_user_tasks(message.from_user.id)
    response = format_tasks(tasks)
    bot.reply_to(message, response)


@bot.message_handler(commands=['done'])
def done(message):
    try:
        task_id = int(message.text[len('/done '):].strip())
        if mark_done(message.from_user.id, task_id):
            bot.reply_to(message, f"[✓] Задача №{task_id} выполнена!")
        else:
            bot.reply_to(message, f"[✗] Задача №{task_id} не найдена.")
    except ValueError:
        bot.reply_to(message, "[!] Укажите номер задачи после /done")
    except Exception as e:
        logger.error(f"Ошибка в команде /done: {e}")

@bot.message_handler(commands=['delete'])
def delete(message):
    try:
        task_id = int(message.text[len('/delete '):].strip())
        if delete_task(message.from_user.id, task_id):
            bot.reply_to(message, f"[✓] Задача №{task_id} удалена!")
        else:
            bot.reply_to(message, f"[✗] Задача №{task_id} не найдена.")
    except ValueError:
        bot.reply_to(message, "[!] Укажите номер задачи после /delete")
    except Exception as e:
        logger.error(f"Ошибка в команде /delete: {e}")


@bot.message_handler(commands=['export'])
def export(message):
    if export_to_csv(message.from_user.id):
        bot.reply_to(message, "[✓] Задачи экспортированы в CSV-файл!")
    else:
        bot.reply_to(message, "[!] Не удалось экспортировать задачи в CSV.")


@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    # Список команд, которые НЕ должны создавать задачи
    commands = ['/start', '/list', '/done', '/delete', '/export']
    
    
    # Если сообщение — это команда, обрабатываем её штатно
    if message.text and message.text.startswith('/'):
        if message.text in commands:
            return  # Пусть другие обработчики разберутся
        else:
            bot.reply_to(
                message,
                "Неизвестная команда. Используйте:\n"
                "/start — справка\n"
                "/list — показать задачи\n"
                "/done <номер> — отметить выполненную\n"
                "/delete <номер> — удалить задачу\n"
                "/export — экспорт в CSV"
            )
            return
    
    # Если это не команда — создаём задачу
    task_id = add_task(message.from_user.id, message.text)
    if task_id:
        bot.reply_to(message, f"[✓] Задача №{task_id} добавлена!")
    else:
        bot.reply_to(message, "[!] Не удалось добавить задачу.")


if __name__ == '__main__':
    logger.info("Бот запущен. Ожидание сообщений...")
    
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
