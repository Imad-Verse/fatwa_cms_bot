"""
مدير قاعدة بيانات البوت (core/bot_db.py)
---------------------------------------
يتولى إدارة قاعدة البيانات التشغيلية للبوت، والتي تشمل:
- بيانات المستخدمين (المشتركين).
- المسؤولين (الأدمن).
- القنوات والمجموعات المضافة للنشر التلقائي.
- الإعدادات العامة للبوت.
- قائمة المفضلات لكل مستخدم.
- سجل العمليات والأوامر.
"""

import logging
import sqlite3
import time
from typing import Dict, List, Optional

from core.config import BOT_DB_NAME, OWNER_ID

logger = logging.getLogger(__name__)


class BotDatabaseManager:
    """إدارة البيانات التشغيلية الداخلية للبوت (المستخدمين، الإعدادات، القنوات)."""

    def __init__(self, db_name: str = BOT_DB_NAME, max_retries: int = 3, retry_delay: float = 1.0):
        """
        تهيئة مدير قاعدة البيانات.
        
        Args:
            db_name: اسم ملف قاعدة البيانات.
            max_retries: أقصى عدد لمحاولات إعادة التنفيذ عند حدوث قفل (Lock).
            retry_delay: الوقت الفاصل بين المحاولات بالثواني.
        """
        self.db_name = db_name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.init_db()

    def execute_with_retry(self, func, *args, **kwargs):
        """
        تنفيذ دالة مع إعادة المحاولة في حال كانت قاعدة البيانات مقفلة (Locked).
        """
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < self.max_retries - 1:
                    logger.warning(f"Database locked, retrying ({attempt + 1}/{self.max_retries})...")
                    time.sleep(self.retry_delay)
                    continue
                raise
            except Exception as e:
                logger.error(f"Database error: {e}")
                raise

    def get_connection(self):
        """إنشاء اتصال مع قاعدة البيانات وتفعيل وضع الأداء العالي (WAL)."""
        try:
            conn = sqlite3.connect(self.db_name, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                # إعدادات تحسين الأداء لـ SQLite
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA busy_timeout = 5000")
            except Exception as e:
                logger.warning(f"SQLite PRAGMA setup failed: {e}")
            return conn
        except Exception as e:
            logger.error(f"Error connecting to database: {e}")
            raise

    def init_db(self):
        def _init():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()

                c.execute(
                    """CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        full_name TEXT,
                        joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        is_blocked INTEGER DEFAULT 0,
                        blocked_at TEXT
                    )"""
                )

                # Ensure new columns exist for older DBs
                c.execute("PRAGMA table_info(users)")
                cols = {row["name"] for row in c.fetchall()}
                if "is_blocked" not in cols:
                    c.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
                if "blocked_at" not in cols:
                    c.execute("ALTER TABLE users ADD COLUMN blocked_at TEXT")

                c.execute(
                    """CREATE TABLE IF NOT EXISTS admins (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS command_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        command TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS favorites (
                        user_id INTEGER NOT NULL,
                        fatwa_id INTEGER NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, fatwa_id)
                    )"""
                )

                c.execute("PRAGMA table_info(favorites)")
                favorite_cols = {row["name"] for row in c.fetchall()}
                if "created_at" not in favorite_cols:
                    c.execute("ALTER TABLE favorites ADD COLUMN created_at TEXT")
                    c.execute(
                        "UPDATE favorites SET created_at = CURRENT_TIMESTAMP "
                        "WHERE created_at IS NULL OR created_at = ''"
                    )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS channels (
                        chat_id INTEGER PRIMARY KEY,
                        title TEXT,
                        username TEXT,
                        type TEXT,
                        status TEXT,
                        added_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )"""
                )

                c.execute(
                    """CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )"""
                )

                # Indexes
                c.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user_id ON favorites(user_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_favorites_fatwa_id ON favorites(fatwa_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_channels_type ON channels(type)")

                # Defaults
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish', '0')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_specific', '0')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_category_id', '')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_topic_ids', '')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_publish_time', '12:00')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_scheduled_fatwa_number', '')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('weekly_report_weekday', '4')")  # Friday
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('weekly_report_time', '08:00')")
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_mode', '0')")

                # Migrate old defaults (Mon 12:00) to requested schedule (Fri 08:00)
                try:
                    c.execute("SELECT value FROM settings WHERE key = 'weekly_report_weekday'")
                    row = c.fetchone()
                    if not row or row["value"] in (None, "", "0"):
                        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('weekly_report_weekday', '4')")

                    c.execute("SELECT value FROM settings WHERE key = 'weekly_report_time'")
                    row = c.fetchone()
                    if not row or row["value"] in (None, "", "12:00"):
                        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('weekly_report_time', '08:00')")

                    c.execute("SELECT value FROM settings WHERE key = 'daily_publish_time'")
                    row = c.fetchone()
                    if not row or row["value"] in (None, "", "12:00"):
                        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_publish_time', '12:00')")
                except Exception as e:
                    logger.warning(f"Failed to migrate weekly report schedule: {e}")

                # Ensure owner is admin
                if OWNER_ID:
                    c.execute(
                        "INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)",
                        (OWNER_ID, None),
                    )

                conn.commit()
            finally:
                if conn:
                    conn.close()

        self.execute_with_retry(_init)

    # Users
    def add_user(self, user_id: int, username: str = None, full_name: str = None):
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = """
                    INSERT INTO users (user_id, username, full_name, joined_at, is_blocked, blocked_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, 0, NULL)
                    ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    is_blocked=0,
                    blocked_at=NULL
                """
                c.execute(sql, (user_id, username, full_name))
                conn.commit()
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_add)

    def user_exists(self, user_id: int) -> bool:
        def _check():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
                return c.fetchone() is not None
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_check)

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        normalized = (username or "").strip().lstrip("@")
        if not normalized:
            return None

        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    """
                    SELECT user_id, username, full_name
                    FROM users
                    WHERE username IS NOT NULL
                      AND LOWER(username) = LOWER(?)
                    ORDER BY joined_at DESC
                    LIMIT 1
                    """,
                    (normalized,),
                )
                row = c.fetchone()
                return dict(row) if row else None
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def get_all_bot_users(self) -> List[int]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    "SELECT user_id FROM users WHERE user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                )
                return [row[0] for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def get_active_users(self, limit: int = None, offset: int = 0) -> List[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = """
                    SELECT user_id, username, full_name
                    FROM users
                    WHERE COALESCE(is_blocked, 0) = 0 AND user_id > 0
                      AND user_id NOT IN (SELECT chat_id FROM channels)
                    ORDER BY joined_at DESC
                """
                params = []
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def get_active_users_count(self) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(is_blocked, 0) = 0 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                )
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_count)

    def get_inactive_users(self, limit: int = None, offset: int = 0) -> List[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = """
                    SELECT user_id, username, full_name, blocked_at
                    FROM users
                    WHERE COALESCE(is_blocked, 0) = 1 AND user_id > 0
                      AND user_id NOT IN (SELECT chat_id FROM channels)
                    ORDER BY COALESCE(blocked_at, joined_at) DESC
                """
                params = []
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def get_inactive_users_count(self) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(is_blocked, 0) = 1 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                )
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_count)

    def get_active_user_ids(self) -> List[int]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    "SELECT user_id FROM users WHERE COALESCE(is_blocked, 0) = 0 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                )
                return [row[0] for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def set_user_blocked(self, user_id: int, blocked: bool = True) -> bool:
        def _set():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                if blocked:
                    c.execute(
                        "UPDATE users SET is_blocked = 1, blocked_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (user_id,)
                    )
                else:
                    c.execute(
                        "UPDATE users SET is_blocked = 0, blocked_at = NULL WHERE user_id = ?",
                        (user_id,)
                    )
                conn.commit()
                return True
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_set)

    def remove_user(self, user_id: int) -> bool:
        def _remove():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error removing user: {e}")
                return False
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_remove)

    # Admins
    def is_admin(self, user_id: int) -> bool:
        def _check():
            if user_id == OWNER_ID:
                return True
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
                return c.fetchone() is not None
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_check)

    def get_admins(self) -> List[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT user_id, username FROM admins")
                return [dict(r) for r in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def add_admin(self, user_id: int, username: str = None) -> bool:
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("INSERT INTO admins (user_id, username) VALUES (?, ?)", (user_id, username))
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_add)

    # Settings
    def get_setting(self, key: str, default: str = None) -> str:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT value FROM settings WHERE key = ?", (key,))
                row = c.fetchone()
                return row["value"] if row else default
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def set_setting(self, key: str, value: str) -> bool:
        def _set():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error setting value: {e}")
                return False
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_set)

    # Channels
    def add_channel(self, chat_id: int, title: str, username: str, chat_type: str, status: str = "active") -> bool:
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = """
                    INSERT INTO channels (chat_id, title, username, type, status, added_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id) DO UPDATE SET
                    title=excluded.title,
                    username=excluded.username,
                    type=excluded.type,
                    status=excluded.status,
                    added_at=CURRENT_TIMESTAMP
                """
                c.execute(sql, (chat_id, title, username, chat_type, status))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding channel: {e}")
                return False
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_add)

    def channel_exists(self, chat_id: int) -> bool:
        def _check():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT 1 FROM channels WHERE chat_id = ?", (chat_id,))
                return c.fetchone() is not None
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_check)

    def update_channel_status(self, chat_id: int, status: str) -> bool:
        def _update():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("UPDATE channels SET status = ? WHERE chat_id = ?", (status, chat_id))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error updating channel status: {e}")
                return False
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_update)

    def remove_channel(self, chat_id: int) -> bool:
        def _remove():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id,))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error removing channel: {e}")
                return False
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_remove)

    def get_channels(self, status: str = None, chat_type: str = None) -> List[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT * FROM channels WHERE 1=1"
                params = []
                if status:
                    sql += " AND status = ?"
                    params.append(status)
                if chat_type:
                    if chat_type == "group":
                        sql += " AND (type = 'group' OR type = 'supergroup')"
                    else:
                        sql += " AND type = ?"
                        params.append(chat_type)
                c.execute(sql, params)
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    # Favorites
    def toggle_favorite(self, user_id: int, fatwa_id: int) -> bool:
        def _toggle():
            conn = self.get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT 1 FROM favorites WHERE user_id = ? AND fatwa_id = ?", (user_id, fatwa_id))
                exists = c.fetchone()
                if exists:
                    c.execute("DELETE FROM favorites WHERE user_id = ? AND fatwa_id = ?", (user_id, fatwa_id))
                    conn.commit()
                    return False
                c.execute("INSERT INTO favorites (user_id, fatwa_id) VALUES (?, ?)", (user_id, fatwa_id))
                conn.commit()
                return True
            finally:
                conn.close()

        return self.execute_with_retry(_toggle)

    def is_favorite(self, user_id: int, fatwa_id: int) -> bool:
        def _check():
            conn = self.get_connection()
            try:
                c = conn.cursor()
                c.execute("SELECT 1 FROM favorites WHERE user_id = ? AND fatwa_id = ?", (user_id, fatwa_id))
                return c.fetchone() is not None
            finally:
                conn.close()

        return self.execute_with_retry(_check)

    def get_user_favorites(self, user_id: int, limit: int = None, offset: int = 0) -> List[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("PRAGMA table_info(favorites)")
                favorite_cols = {row["name"] for row in c.fetchall()}
                has_created_at = "created_at" in favorite_cols
                select_columns = "fatwa_id, created_at" if has_created_at else "fatwa_id, '' AS created_at"
                order_clause = "ORDER BY datetime(created_at) DESC, rowid DESC" if has_created_at else "ORDER BY rowid DESC"
                sql = (
                    f"SELECT {select_columns} "
                    "FROM favorites "
                    "WHERE user_id = ? "
                    f"{order_clause}"
                )
                params = [user_id]
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def get_user_favorite_ids(self, user_id: int, limit: int = None, offset: int = 0) -> List[int]:
        favorites = self.get_user_favorites(user_id, limit=limit, offset=offset)
        return [int(row["fatwa_id"]) for row in favorites]

    def remove_favorites_for_fatwa(self, fatwa_id: int) -> None:
        def _remove():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("DELETE FROM favorites WHERE fatwa_id = ?", (fatwa_id,))
                conn.commit()
            finally:
                if conn:
                    conn.close()

        self.execute_with_retry(_remove)

    def get_top_favorites(self, limit: int = 5) -> List[Dict]:
        def _get_top():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = """
                    SELECT fatwa_id, COUNT(*) as fav_count
                    FROM favorites
                    GROUP BY fatwa_id
                    ORDER BY fav_count DESC
                    LIMIT ?
                """
                c.execute(sql, (limit,))
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get_top)

    # Stats
    def get_statistics(self) -> Dict:
        def _stats():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                stats = {}
                # Count channels/groups where the bot is still registered in DB
                # (includes active + inactive بسبب نقص الصلاحيات).
                c.execute("SELECT COUNT(*) FROM channels WHERE type='channel'")
                stats["channels"] = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM channels WHERE (type='group' OR type='supergroup')")
                stats["groups"] = c.fetchone()[0]
                c.execute(
                    "SELECT COUNT(*) FROM users WHERE user_id > 0 AND COALESCE(is_blocked, 0) = 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                )
                stats["subscribers"] = c.fetchone()[0]
                return stats
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_stats)
