import os
import logging
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import json
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

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
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5
)

def cache_key(user_id):
    return f"tasks:{user_id}"

def invalidate_cache(user_id):
    """Очистить кэш для пользователя"""
    try:
        redis_client.delete(cache_key(user_id))
        logger.debug(f"Кэш очищен для user_id={user_id}")
    except Exception as e:
        logger.error(f"Ошибка при очистке кэша для user_id={user_id}: {e}")

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
                );
            """)
        conn.commit()
        logger.info("Таблица tasks проверена/создана")
    except Exception as e:
        logger.error(f"Ошибка при создании таблицы tasks: {e}")
    finally:
        if conn:
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
                task['created_at'] = task['created_at'].isoformat()
                tasks.append(task)
            redis_client.set(cache_key(user_id), json.dumps(tasks))
            return tasks
    except Exception as e:
        logger.error(f"Ошибка загрузки задач из БД: {e}")
        return []
    finally:
        if conn:
            conn.close()

def save_task_to_db(user_id, text):
    """Добавить задачу в PostgreSQL"""
    conn = get_db_conn()
    if not conn:
        return None

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT MAX(task_id_in_list) FROM tasks WHERE user_id = %s",
                    (user_id,)
                )
                max_id = cur.fetchone()[0]
                new_id = (max_id or 0) + 1

                cur.execute(
                    "INSERT INTO tasks (user_id, text, task_id_in_list) "
                    "VALUES (%s, %s, %s) "
                    "RETURNING task_id_in_list",
                    (user_id, text, new_id)
                )
                task_id = cur.fetchone()[0]
                conn.commit()
                invalidate_cache(user_id)
                return task_id
    except Exception as e:
        logger.error(f"Ошибка сохранения задачи в БД: {e}")
        return None
    finally:
        if conn:
            conn.close()

def mark_done_in_db(user_id, task_id_in_list):
    """Отметить задачу как выполненную"""
    conn = get_db_conn()
    if not conn:
        logger.error("Не удалось установить соединение с БД")
        return False

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tasks SET done = TRUE WHERE user_id = %s AND task_id_in_list = %s",
                    (user_id, task_id_in_list)
                )
                if cur.rowcount == 0:
                    logger.warning(f"Задача не найдена: user_id={user_id}, task_id_in_list={task_id_in_list}")
                    return False
                conn.commit()
                invalidate_cache(user_id)
                logger.info(f"Задача отмечена как выполненная: user_id={user_id}, task_id_in_list={task_id_in_list}")
                return True
    except Exception as e:
        logger.error(f"Ошибка при отметке задачи как выполненной (user_id={user_id}, task_id_in_list={task_id_in_list}): {e}")
        return False
    finally:
        if conn:
            conn.close()

def delete_task_from_db(user_id, task_id_in_list):
    """Удалить задачу и пересчитать task_id_in_list"""
    conn = get_db_conn()
    if not conn:
        logger.error("Не удалось установить соединение с БД")
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
                    logger.warning(f"Задача не найдена при удалении: user_id={user_id}, task_id_in_list={task_id_in_list}")
                    return False

                # Пересчитываем task_id_in_list для оставшихся задач
                cur.execute(
                    """
                    UPDATE tasks
                    SET task_id_in_list = subquery.new_id
                    FROM (
                        SELECT id, ROW_NUMBER() OVER (ORDER BY created_at) AS new_id
                        FROM tasks
                        WHERE user_id = %s
                    ) AS subquery
                    WHERE tasks.id = subquery.id
                    """,
                    (user_id,)
                )
                conn.commit()
                invalidate_cache(user_id)
                logger.info(f"Задача удалена: user_id={user_id}, task_id_in_list={task_id_in_list}")
                return True
    except Exception as e:
        logger.error(f"Ошибка при удалении задачи (user_id={user_id}, task_id_in_list={task_id_in_list}): {e}")
        return False
    finally:
        if conn:
            conn.close()
def clear_all_tasks_db(user_id):
    """Удалить все задачи пользователя и вернуть количество удалённых"""
    conn = get_db_conn()
    if not conn:
        logger.error("Не удалось установить соединение с БД")
        return False, 0


    try:
        with conn:
            with conn.cursor() as cur:
                # Считаем количество задач перед удалением
                cur.execute(
                    "SELECT COUNT(*) FROM tasks WHERE user_id = %s",
                    (user_id,)
                )
                count_before = cur.fetchone()[0]

                if count_before == 0:
                    return True, 0  # Нет задач — успешно, но удалено 0


                # Удаляем все задачи
                cur.execute(
                    "DELETE FROM tasks WHERE user_id = %s",
                    (user_id,)
                )
                conn.commit()
                invalidate_cache(user_id)

                logger.info(f"Удалены все задачи для user_id={user_id} (количество: {count_before})")
                return True, count_before
    except Exception as e:
        logger.error(f"Ошибка при удалении всех задач для user_id={user_id}: {e}")
        return False, 0
    finally:
        if conn:
            conn.close()



def done_all_tasks_db(user_id):
    """Отметить все задачи пользователя как выполненные"""
    conn = get_db_conn()
    if not conn:
        logger.error("Не удалось установить соединение с БД")
        return False, 0

    try:
        with conn:
            with conn.cursor() as cur:
                # Считаем количество невыполненных задач
                cur.execute(
                    "SELECT COUNT(*) FROM tasks WHERE user_id = %s AND done = FALSE",
                    (user_id,)
                )
                count_pending = cur.fetchone()[0]

                if count_pending == 0:
                    return True, 0  # Все уже выполнены — успешно, но обновлено 0

                # Отмечаем все задачи как выполненные
                cur.execute(
                    "UPDATE tasks SET done = TRUE WHERE user_id = %s",
                    (user_id,)
                )
                conn.commit()
                invalidate_cache(user_id)

                logger.info(f"Все задачи отмечены как выполненные для user_id={user_id} (обновлено: {count_pending})")
                return True, count_pending
    except Exception as e:
        logger.error(f"Ошибка при отметке всех задач как выполненных для user_id={user_id}: {e}")
        return False, 0
    finally:
        if conn:
            conn.close()



def get_user_tasks(user_id):
    """Получить задачи пользователя (из кэша Redis или из PostgreSQL)"""
    # 1. Проверяем кэш Redis
    cached = redis_client.get(cache_key(user_id))
    if cached:
        try:
            tasks = json.loads(cached)
            logger.debug(f"Задачи для user_id={user_id} загружены из Redis")
            return tasks
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка декодирования JSON из Redis для user_id={user_id}: {e}")


    # 2. Если кэша нет или он невалидный — загружаем из БД
    tasks = load_tasks_from_db(user_id)
    logger.debug(f"Задачи для user_id={user_id} загружены из PostgreSQL")
    return tasks
