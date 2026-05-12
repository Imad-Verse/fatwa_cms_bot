import logging
import aiosqlite
import asyncio
from typing import Dict, List, Optional, Tuple

from core.config import BOT_DB_PATH, OWNER_ID
from core.database.base import DatabaseBase
from core.utils import cache, cached_async

logger = logging.getLogger(__name__)


class BotDatabaseManager(DatabaseBase):
    """
    مدير البيانات التشغيلية للبوت (Internal Bot Manager).
    مسؤول عن:
    1. إدارة المستخدمين (المشتركين والمحظورين).
    2. إدارة المسؤولين (Admins) وصلاحياتهم.
    3. إدارة القنوات والمجموعات المشتركة في نظام النشر.
    4. حفظ واسترجاع إعدادات النظام (Settings).
    5. إدارة المفضلة للمستخدمين.
    """

    def __init__(self, db_name: str = BOT_DB_PATH, max_retries: int = 3, retry_delay: float = 1.0):
        super().__init__(db_name, max_retries, retry_delay)

    async def init_db(self):
        if self._initialized:
            return
            
        async def _init():
            conn = None
            try:
                conn = await self.get_connection()
                
                await conn.execute(
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
                async with conn.execute("PRAGMA table_info(users)") as cursor:
                    cols = {row["name"] for row in await cursor.fetchall()}
                if "is_blocked" not in cols:
                    await conn.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
                if "blocked_at" not in cols:
                    await conn.execute("ALTER TABLE users ADD COLUMN blocked_at TEXT")

                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS admins (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT
                    )"""
                )

                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS command_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        command TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )"""
                )

                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS favorites (
                        user_id INTEGER NOT NULL,
                        fatwa_id INTEGER NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, fatwa_id)
                    )"""
                )

                async with conn.execute("PRAGMA table_info(favorites)") as cursor:
                    favorite_cols = {row["name"] for row in await cursor.fetchall()}
                if "created_at" not in favorite_cols:
                    await conn.execute("ALTER TABLE favorites ADD COLUMN created_at TEXT")
                    await conn.execute(
                        "UPDATE favorites SET created_at = CURRENT_TIMESTAMP "
                        "WHERE created_at IS NULL OR created_at = ''"
                    )

                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS channels (
                        chat_id INTEGER PRIMARY KEY,
                        title TEXT,
                        username TEXT,
                        type TEXT,
                        status TEXT,
                        added_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )"""
                )

                await conn.execute(
                    """CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )"""
                )

                # Indexes
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user_id ON favorites(user_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_favorites_fatwa_id ON favorites(fatwa_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_type ON channels(type)")

                # Defaults
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish', '0')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_specific', '0')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_category_id', '')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_topic_ids', '')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_publish_time', '12:00')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_publish_scheduled_fatwa_number', '')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('weekly_report_weekday', '4')")  # Friday
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('weekly_report_time', '08:00')")
                await conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_mode', '0')")

                # Migrate old defaults (Mon 12:00) to requested schedule (Fri 08:00)
                try:
                    async with conn.execute("SELECT value FROM settings WHERE key = 'weekly_report_weekday'") as cursor:
                        row = await cursor.fetchone()
                        if not row or row["value"] in (None, "", "0"):
                            await conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('weekly_report_weekday', '4')")

                    async with conn.execute("SELECT value FROM settings WHERE key = 'weekly_report_time'") as cursor:
                        row = await cursor.fetchone()
                        if not row or row["value"] in (None, "", "12:00"):
                            await conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('weekly_report_time', '08:00')")

                    async with conn.execute("SELECT value FROM settings WHERE key = 'daily_publish_time'") as cursor:
                        row = await cursor.fetchone()
                        if not row or row["value"] in (None, "", "12:00"):
                            await conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_publish_time', '12:00')")
                except Exception as e:
                    logger.warning(f"Failed to migrate weekly report schedule: {e}")

                # Ensure owner is admin
                if OWNER_ID:
                    await conn.execute(
                        "INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)",
                        (OWNER_ID, None),
                    )

                await conn.commit()
            finally:
                if conn:
                    await conn.close()

        await self.execute_with_retry(_init)
        self._initialized = True

    # Users
    async def add_user(self, user_id: int, username: str = None, full_name: str = None):
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
                sql = """
                    INSERT INTO users (user_id, username, full_name, joined_at, is_blocked, blocked_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, 0, NULL)
                    ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    is_blocked=0,
                    blocked_at=NULL
                """
                await conn.execute(sql, (user_id, username, full_name))
                await conn.commit()
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_add)

    async def user_exists(self, user_id: int) -> bool:
        async def _check():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)) as cursor:
                    return await cursor.fetchone() is not None
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_check)

    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        normalized = (username or "").strip().lstrip("@")
        if not normalized:
            return None

        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    """
                    SELECT user_id, username, full_name
                    FROM users
                    WHERE username IS NOT NULL
                      AND LOWER(username) = LOWER(?)
                    ORDER BY joined_at DESC
                    LIMIT 1
                    """,
                    (normalized,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    async def get_all_bot_users(self) -> List[int]:
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "SELECT user_id FROM users WHERE user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                ) as cursor:
                    return [row[0] for row in await cursor.fetchall()]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    async def get_active_users(self, limit: int = None, offset: int = 0) -> Tuple[List[Dict], int]:
        """Retrieve active users (not blocked) with total count."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                
                # Get Total Count
                async with conn.execute(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(is_blocked, 0) = 0 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                ) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0] if row else 0

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
                
                async with conn.execute(sql, params) as cursor:
                    results = [dict(row) for row in await cursor.fetchall()]
                return results, total_count
            finally:
                if conn:
                    await conn.close()
        return await self.execute_with_retry(_get)

    async def get_active_users_count(self) -> int:
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(is_blocked, 0) = 0 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_count)

    async def get_inactive_users(self, limit: int = None, offset: int = 0) -> Tuple[List[Dict], int]:
        """Retrieve inactive users (blocked) with total count."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                
                # Get Total Count
                async with conn.execute(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(is_blocked, 0) = 1 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                ) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0] if row else 0

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
                async with conn.execute(sql, params) as cursor:
                    results = [dict(row) for row in await cursor.fetchall()]
                return results, total_count
            finally:
                if conn:
                    await conn.close()
        return await self.execute_with_retry(_get)

    async def get_inactive_users_count(self) -> int:
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "SELECT COUNT(*) FROM users WHERE COALESCE(is_blocked, 0) = 1 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_count)

    async def get_active_user_ids(self) -> List[int]:
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "SELECT user_id FROM users WHERE COALESCE(is_blocked, 0) = 0 AND user_id > 0 "
                    "AND user_id NOT IN (SELECT chat_id FROM channels)"
                ) as cursor:
                    return [row[0] for row in await cursor.fetchall()]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    async def set_user_blocked(self, user_id: int, blocked: bool = True) -> bool:
        async def _set():
            conn = None
            try:
                conn = await self.get_connection()
                if blocked:
                    await conn.execute(
                        "UPDATE users SET is_blocked = 1, blocked_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (user_id,)
                    )
                else:
                    await conn.execute(
                        "UPDATE users SET is_blocked = 0, blocked_at = NULL WHERE user_id = ?",
                        (user_id,)
                    )
                await conn.commit()
                return True
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_set)

    async def remove_user(self, user_id: int) -> bool:
        async def _remove():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
                await conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error removing user: {e}")
                return False
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_remove)

    # Admins
    @cached_async(ttl=600)  # تخزين لمدة 10 دقائق
    async def is_admin(self, user_id: int) -> bool:
        async def _check():
            if user_id == OWNER_ID:
                return True
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)) as cursor:
                    return await cursor.fetchone() is not None
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_check)

    async def get_admins(self) -> List[Dict]:
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT user_id, username FROM admins") as cursor:
                    return [dict(r) for r in await cursor.fetchall()]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    async def add_admin(self, user_id: int, username: str = None) -> bool:
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("INSERT INTO admins (user_id, username) VALUES (?, ?)", (user_id, username))
                await conn.commit()
                # إبطال الكاش
                cache.delete(f"is_admin:({self}, {user_id}):{{}}")
                return True
            except aiosqlite.IntegrityError:
                return False
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_add)

    # Settings
    @cached_async(ttl=300)
    async def get_setting(self, key: str, default: str = None) -> str:
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                    row = await cursor.fetchone()
                    return row["value"] if row else default
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    async def set_setting(self, key: str, value: str) -> bool:
        async def _set():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
                await conn.commit()
                # إبطال الكاش الخاص بهذا المفتاح
                cache.delete(f"get_setting:({self}, '{key}'):{{}}")
                return True
            except Exception as e:
                logger.error(f"Error setting value: {e}")
                return False
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_set)

    # Channels
    async def add_channel(self, chat_id: int, title: str, username: str, chat_type: str, status: str = "active") -> bool:
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
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
                await conn.execute(sql, (chat_id, title, username, chat_type, status))
                await conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error adding channel: {e}")
                return False
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_add)

    async def channel_exists(self, chat_id: int) -> bool:
        async def _check():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT 1 FROM channels WHERE chat_id = ?", (chat_id,)) as cursor:
                    return await cursor.fetchone() is not None
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_check)

    async def update_channel_status(self, chat_id: int, status: str) -> bool:
        async def _update():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("UPDATE channels SET status = ? WHERE chat_id = ?", (status, chat_id))
                await conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error updating channel status: {e}")
                return False
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_update)

    async def remove_channel(self, chat_id: int) -> bool:
        async def _remove():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id,))
                await conn.commit()
                return True
            except Exception as e:
                logger.error(f"Error removing channel: {e}")
                return False
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_remove)

    async def get_channels(self, status: str = None, chat_type: str = None) -> List[Dict]:
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
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
                async with conn.execute(sql, params) as cursor:
                    return [dict(row) for row in await cursor.fetchall()]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    # Favorites
    async def toggle_favorite(self, user_id: int, fatwa_id: int) -> bool:
        async def _toggle():
            conn = await self.get_connection()
            try:
                async with conn.execute("SELECT 1 FROM favorites WHERE user_id = ? AND fatwa_id = ?", (user_id, fatwa_id)) as cursor:
                    exists = await cursor.fetchone()
                    if exists:
                        await conn.execute("DELETE FROM favorites WHERE user_id = ? AND fatwa_id = ?", (user_id, fatwa_id))
                        await conn.commit()
                        return False
                    await conn.execute("INSERT INTO favorites (user_id, fatwa_id) VALUES (?, ?)", (user_id, fatwa_id))
                    await conn.commit()
                    return True
            finally:
                await conn.close()

        return await self.execute_with_retry(_toggle)

    async def is_favorite(self, user_id: int, fatwa_id: int) -> bool:
        async def _check():
            conn = await self.get_connection()
            try:
                async with conn.execute("SELECT 1 FROM favorites WHERE user_id = ? AND fatwa_id = ?", (user_id, fatwa_id)) as cursor:
                    return await cursor.fetchone() is not None
            finally:
                await conn.close()

        return await self.execute_with_retry(_check)

    async def get_user_favorites(self, user_id: int, limit: int = None, offset: int = 0) -> List[Dict]:
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("PRAGMA table_info(favorites)") as cursor:
                    favorite_cols = {row["name"] for row in await cursor.fetchall()}
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
                async with conn.execute(sql, params) as cursor:
                    return [dict(row) for row in await cursor.fetchall()]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get)

    async def get_user_favorite_ids(self, user_id: int, limit: int = None, offset: int = 0) -> List[int]:
        favorites = await self.get_user_favorites(user_id, limit=limit, offset=offset)
        return [int(row["fatwa_id"]) for row in favorites]

    async def remove_favorites_for_fatwa(self, fatwa_id: int) -> None:
        async def _remove():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("DELETE FROM favorites WHERE fatwa_id = ?", (fatwa_id,))
                await conn.commit()
            finally:
                if conn:
                    await conn.close()

        await self.execute_with_retry(_remove)

    async def get_top_favorites(self, limit: int = 5) -> List[Dict]:
        async def _get_top():
            conn = None
            try:
                conn = await self.get_connection()
                sql = """
                    SELECT fatwa_id, COUNT(*) as fav_count
                    FROM favorites
                    GROUP BY fatwa_id
                    ORDER BY fav_count DESC
                    LIMIT ?
                """
                async with conn.execute(sql, (limit,)) as cursor:
                    return [dict(row) for row in await cursor.fetchall()]
            finally:
                if conn:
                    await conn.close()

        return await self.execute_with_retry(_get_top)

    # Stats
    async def get_statistics(self) -> Dict:
        """Fetch bot-wide statistics (Optimized single query)."""
        async def _stats():
            conn = None
            try:
                conn = await self.get_connection()
                sql = """
                    SELECT 
                        (SELECT COUNT(*) FROM channels WHERE type='channel') as channels,
                        (SELECT COUNT(*) FROM channels WHERE type IN ('group', 'supergroup')) as groups,
                        (SELECT COUNT(*) FROM users WHERE user_id > 0 AND COALESCE(is_blocked, 0) = 0 
                         AND user_id NOT IN (SELECT chat_id FROM channels)) as subscribers
                """
                async with conn.execute(sql) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return {
                            "channels": row["channels"],
                            "groups": row["groups"],
                            "subscribers": row["subscribers"]
                        }
                    return {"channels": 0, "groups": 0, "subscribers": 0}
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_stats)
