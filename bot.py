import os
import logging
import time
import csv
from datetime import datetime
from dotenv import load_dotenv
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import json

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

# Подключение к PostgreSQL
def get_db_conn():
    try:
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT'),
            dbname=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASS')
        )
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к PostgreSQL: {e}")
        return None

# Подключение к Redis
redis_client = redis.Redis(
    host=os.getenv('REDIS_HOST'),
    port=int(os.getenv('REDIS_PORT')),
    db=int(os.getenv('REDIS_DB')),
    decode_responses=True
)

def cache_key(user_id):
    return f"tasks:{user_id}"

def init_db():
    """Создать таблицу tasks, если её нет"""
    conn = get_db_conn()
    if not conn:
        logger.error("Не удалось подключиться к БД для инициализации")
        return
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    text TEXT NOT NULL,
                    done BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW(),
                    task_id_in_list INT NOT NULL
                )
            """)
        conn.commit()
        logger.info("Таблица tasks проверена/создана")
    except Exception as e:
        logger.error(f"Ошибка при создании таблицы tasks: {e}")
    finally:
        conn.close()

def load_tasks_from_db(user_id):
    """Загрузить задачи пользователя из PostgreSQL"""
    conn = get_db_conn()
    if not conn:
        return []
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, text, done, created_at, task_id_in_list "
                "FROM tasks "
                "WHERE user_id = %s "
                "ORDER BY task_id_in_list",
                (user_id,)
            )
            tasks = []
            for row in cur.fetchall():
                task = dict(row)
                # Преобразуем datetime в строку ISO 8601
                task['created_at'] = task['created_at'].isoformat()
                tasks.append(task)
            # Кэшируем в Redis
            redis_client.set(cache_key(user_id), json.dumps(tasks))
            return tasks
    except Exception as e:
        logger.error(f"Ошибка загрузки задач из БД: {e}")
        return []
    finally:
        conn.close()

def save_task_to_db(user_id, text):
    """Добавить задачу в PostgreSQL"""
    conn = get_db_conn()
    if not conn:
        return None
    
    try:
        with conn:
            with conn.cursor() as cur:
                # Получаем текущий максимальный task_id_in_list для пользователя
                cur.execute(
                    "SELECT MAX(task_id_in_list) FROM tasks WHERE user_id = %s",
                    (user_id,)
                )
                max_id = cur.fetchone()[0]
                new_id = (max_id or 0) + 1

                cur.execute(
                    "INSERT INTO tasks (user_id, text, task_id_in_list) "
                    "VALUES (%s, %s, %s) "
                    "RETURNING id, task_id_in_list",
                    (user_id, text, new_id)
                )
                task_id = cur.fetchone()[1]
                conn.commit()
                # Обновляем кэш
                invalidate_cache(user_id)
                return task_id
    except Exception as e:
        logger.error(f"Ошибка сохранения задачи в БД: {e}")
        return None
    finally:
        conn.close()

def mark_done_in_db(user_id, task_id_in_list):
    """Отметить задачу как выполненную"""
    conn = get_db_conn()
    if not conn:
        return False
    
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET done = TRUE WHERE user_id = %s AND task_id_in_list = %s",
                    (user_id, task_id_in_list)
                )
                if cur.rowcount == 0:
                    return False
                conn.commit()
                invalidate_cache(user_id)
                return True
    except Exception as e:
        logger.error(f"Ошибка отметки задачи как выполненной: {e}")
        return False
    finally:
        conn.close()

def delete_task_from_db(user_id, task_id_in_list):
    """Удалить задачу и пересчитать task_id_in_list"""
    conn = get_db_conn()
    if not conn:
        return False
    
    try:
        with conn:
            with conn.cursor() as cur:
                # Удаляем задачу
                cur.execute(
                    "DELETE FROM tasks WHERE user_id = %s AND task_id_in_list = %s",
                    (user_id, task_id_in_list)
                )
                if cur.rowcount == 0:
                    return False

                # Пересчитываем task_id_in_list для оставшихся задач
                cur.execute(
                    "UPDATE tasks "
                    "SET task_id_in_list = subquery.new_id "
                    "FROM ( "
                    "   SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS new_id "
                    "   FROM tasks "
                    "   WHERE user_id = %s "
                    ") AS subquery "
                    "WHERE tasks.id = subquery.id",
                    (user_id,)
                )
                conn.commit()
                invalidate_cache(user_id)
                return True
    except Exception as e:
        logger.error(f"Ошибка удаления задачи: {e}")
        return False
    finally:
        conn.close()

def invalidate_cache(user_id):
    """Очистить кэш Redis для пользователя"""
    redis_client.delete(cache_key(user_id))

def get_user_tasks(user_id):
    """Получить задачи пользователя (из кэша или БД)"""
    cached = redis_client.get(cache_key(user_id))
    if cached:
        return json.loads(cached)
    return load_tasks_from_db(user_id)

def format_tasks(tasks):
    if not tasks:
        return "У вас нет задач."
    lines = []
    for task in tasks:
        status = "[✓]" if task['done'] else "[⬜]"
        # Если created_at — строка, берём первые 19 символов для краткости
        created_str = task['created_at'][:19] if isinstance(task['created_at'], str) else task['created_at']
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
                         "/export — экспортировать задачи в CSV")


@bot.message_handler(commands=['list'])
def list_tasks(message):
    """Показать список задач пользователя"""
    tasks = get_user_tasks(message.from_user.id)
    response = format_tasks(tasks)
    bot.reply_to(message, response)

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

    if mark_done_in_db(message.from_user.id, task_id):
        bot.reply_to(message, f"[✓] Задача №{task_id} отмечена как выполненная!")
    else:
        bot.reply_to(message, "Задача не найдена или уже выполнена.")


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

    if delete_task_from_db(message.from_user.id, task_id):
        bot.reply_to(message, f"[✓] Задача №{task_id} удалена!")
    else:
        bot.reply_to(message, "Задача не найдена.")


@bot.message_handler(commands=['export'])
def export_tasks(message):
    """Экспортировать задачи в CSV"""
    tasks = get_user_tasks(message.from_user.id)
    if not tasks:
        bot.reply_to(message, "У вас нет задач для экспорта.")
        return

    # Создаем CSV
    csv_data = "Номер;Статус;Текст;Дата создания\n"
    for task in tasks:
        status = "Выполнено" if task['done'] else "Не выполнено"
        # Восстанавливаем datetime из строки ISO, если нужно форматировать
        try:
            created_dt = datetime.fromisoformat(task['created_at'])
            formatted_date = created_dt.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            # Если преобразование не удалось, используем исходную строку
            formatted_date = task['created_at']
        csv_data += f"{task['task_id_in_list']};{status};{task['text']};{formatted_date}\n"


    # Отправляем файл
    bot.send_document(
        message.chat.id,
        document=csv_data.encode('utf-8'),
        caption="Ваши задачи (CSV)"
    )

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
        bot.reply_to(message, f"[⬜] Задача №{task_id} добавлена!")
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
