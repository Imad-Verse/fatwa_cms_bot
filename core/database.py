"""
Ù…Ù„Ù Ø¥Ø¯Ø§Ø±Ø© Ù‚Ø§Ø¹Ø¯Ø© Ø§Ù„Ø¨ÙŠØ§Ù†Ø§Øª (database.py)
--------------------------------------
ÙŠØ­ØªÙˆÙŠ Ø¹Ù„Ù‰ ÙØ¦Ø© FatwaDatabaseManager Ø§Ù„ØªÙŠ ØªØ¯ÙŠØ± Ø¹Ù…Ù„ÙŠØ§Øª SQLite Ø§Ù„Ø®Ø§ØµØ© Ø¨Ø¨ÙŠØ§Ù†Ø§Øª Ø§Ù„ÙØªØ§ÙˆÙ‰ ÙÙ‚Ø·:
- Ø¥Ù†Ø´Ø§Ø¡ Ø§Ù„Ø¬Ø¯Ø§ÙˆÙ„ (Schema Ø§Ù„Ø¬Ø¯ÙŠØ¯).
- Ø¥Ø¶Ø§ÙØ©/ØªØ¹Ø¯ÙŠÙ„/Ø­Ø°Ù Ø§Ù„ÙØªØ§ÙˆÙ‰.
- Ø¥Ø¯Ø§Ø±Ø© Ø§Ù„ØªØµÙ†ÙŠÙØ§ØªØŒ Ø§Ù„Ù…ÙˆØ§Ø¶ÙŠØ¹ØŒ Ø§Ù„Ø¹Ù„Ù…Ø§Ø¡ØŒ Ø§Ù„Ù…ØµØ§Ø¯Ø±.
- Ø§Ù„Ø¨Ø­Ø« ÙˆØ§Ù„Ø¥Ø­ØµØ§Ø¦ÙŠØ§Øª Ø§Ù„Ø®Ø§ØµØ© Ø¨Ø§Ù„ÙØªØ§ÙˆÙ‰.
"""

import sqlite3
import time
import logging
import os
from typing import Dict, List, Optional, Tuple, Union
from datetime import datetime
from core.config import FATWAS_DB_NAME

logger = logging.getLogger(__name__)
class FatwaDatabaseManager:
    """Ù…Ø¯ÙŠØ± Ù‚Ø§Ø¹Ø¯Ø© Ø¨ÙŠØ§Ù†Ø§Øª Ø§Ù„ÙØªØ§ÙˆÙ‰ ÙÙ‚Ø· (Redesigned Schema)"""

    def __init__(self, db_name=FATWAS_DB_NAME, max_retries=3, retry_delay=1.0):
        self.db_name = db_name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.init_db()

    def execute_with_retry(self, func, *args, **kwargs):
        """ØªÙ†ÙÙŠØ° Ø¹Ù…Ù„ÙŠØ© Ù…Ø¹ Ø¥Ø¹Ø§Ø¯Ø© Ø§Ù„Ù…Ø­Ø§ÙˆÙ„Ø© ÙÙŠ Ø­Ø§Ù„Ø© Ø§Ù„ÙØ´Ù„"""
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < self.max_retries - 1:
                    logger.warning(f"Database locked, retrying ({attempt + 1}/{self.max_retries})...")
                    time.sleep(self.retry_delay)
                    continue
                else:
                    raise e
            except Exception as e:
                logger.error(f"Database error: {e}")
                raise

    def get_connection(self):
        """Ø§Ù„Ø­ØµÙˆÙ„ Ø¹Ù„Ù‰ Ø§ØªØµØ§Ù„ Ø¨Ù‚Ø§Ø¹Ø¯Ø© Ø§Ù„Ø¨ÙŠØ§Ù†Ø§Øª"""
        try:
            from core.utils import remove_tashkeel, normalize_text
            conn = sqlite3.connect(self.db_name, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA busy_timeout = 5000")
            except Exception as e:
                logger.warning(f"SQLite PRAGMA setup failed: {e}")
            conn.create_function("REMOVE_TASHKEEL", 1, remove_tashkeel)
            conn.create_function("NORMALIZE_TEXT", 1, normalize_text)
            return conn
        except Exception as e:
            logger.error(f"Error connecting to database: {e}")
            raise

    def init_db(self):
        """ØªÙ‡ÙŠØ¦Ø© Ù‚Ø§Ø¹Ø¯Ø© Ø§Ù„Ø¨ÙŠØ§Ù†Ø§Øª Ù…Ø¹ Ø§Ù„Ù‡ÙŠÙƒÙ„Ø© Ø§Ù„Ø¬Ø¯ÙŠØ¯Ø©"""
        def _init_db():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # 1. Ø¬Ø¯ÙˆÙ„ Ø§Ù„Ø¹Ù„Ù…Ø§Ø¡ (scholars)
                c.execute('''CREATE TABLE IF NOT EXISTS scholars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    biography TEXT,
                    website TEXT
                )''')
                logger.info("Checked/Created scholars table")
                # 2. Ø¬Ø¯ÙˆÙ„ Ø§Ù„Ù…ØµØ§Ø¯Ø± (sources)
                c.execute('''CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                )''')
                # 3. Ø¬Ø¯ÙˆÙ„ Ø¹Ù†Ø§ÙˆÙŠÙ† Ø§Ù„Ù…ØµØ§Ø¯Ø± (source_titles)
                c.execute('''CREATE TABLE IF NOT EXISTS source_titles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT,
                    audio_url TEXT,
                    FOREIGN KEY (source_id) REFERENCES sources(id)
                )''')
                # 4. Ø¬Ø¯ÙˆÙ„ Ø§Ù„ØªØµÙ†ÙŠÙØ§Øª (categories)
                c.execute('''CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    type TEXT CHECK(type IN ('fiqh', 'topic')) NOT NULL
                )''')
                logger.info("Checked/Created categories table")
                # 5. Ø¬Ø¯ÙˆÙ„ Ø§Ù„Ù…ÙˆØ§Ø¶ÙŠØ¹ (topics)
                c.execute('''CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )''')
                # 6. Ø¬Ø¯ÙˆÙ„ Ø§Ù„ÙØªØ§ÙˆÙ‰ (fatwas)
                c.execute('''CREATE TABLE IF NOT EXISTS fatwas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fatwa_number INTEGER NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    normalized_answer TEXT,
                    status TEXT CHECK(status IN ('published', 'draft')) NOT NULL,
                    views INTEGER DEFAULT 0,
                    favorites_count INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    scholar_id INTEGER NOT NULL,
                    source_title_id INTEGER NOT NULL,
                    FOREIGN KEY (scholar_id) REFERENCES scholars(id),
                    FOREIGN KEY (source_title_id) REFERENCES source_titles(id)
                )''')
                # Ensure columns exist (for older databases)
                try:
                    c.execute("PRAGMA table_info(fatwas)")
                    columns = [row['name'] for row in c.fetchall()]
                except Exception as e:
                    logger.warning(f"Failed to read fatwas table info: {e}")
                    columns = []

                if 'favorites_count' not in columns:
                    try:
                        c.execute("ALTER TABLE fatwas ADD COLUMN favorites_count INTEGER DEFAULT 0")
                        c.execute("UPDATE fatwas SET favorites_count = 0 WHERE favorites_count IS NULL")
                    except Exception as e:
                        logger.warning(f"Failed to ensure favorites_count column: {e}")

                if 'created_at' not in columns:
                    try:
                        # SQLite doesn't allow non-constant defaults on ALTER TABLE.
                        c.execute("ALTER TABLE fatwas ADD COLUMN created_at TEXT")
                        c.execute("UPDATE fatwas SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at = ''")
                    except Exception as e:
                        logger.warning(f"Failed to ensure created_at column: {e}")

                if 'normalized_answer' not in columns:
                    try:
                        c.execute("ALTER TABLE fatwas ADD COLUMN normalized_answer TEXT")
                    except Exception as e:
                        logger.warning(f"Failed to ensure normalized_answer column: {e}")

                try:
                    c.execute("""
                        SELECT 1
                        FROM fatwas
                        WHERE (normalized_answer IS NULL OR normalized_answer = '')
                        AND answer IS NOT NULL
                        AND TRIM(answer) != ''
                        LIMIT 1
                    """)
                    needs_backfill = c.fetchone() is not None
                    if needs_backfill:
                        c.execute("""
                            UPDATE fatwas
                            SET normalized_answer = NORMALIZE_TEXT(answer)
                            WHERE (normalized_answer IS NULL OR normalized_answer = '')
                            AND answer IS NOT NULL
                            AND TRIM(answer) != ''
                        """)
                except Exception as e:
                    logger.warning(f"Failed to backfill normalized_answer values: {e}")
                # 7. Ø¬Ø¯Ø§ÙˆÙ„ Ø§Ù„Ø±Ø¨Ø· (Junction Tables)
                c.execute('''CREATE TABLE IF NOT EXISTS fatwa_categories (
                    fatwa_id INTEGER NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY (fatwa_id, category_id),
                    FOREIGN KEY (fatwa_id) REFERENCES fatwas(id),
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )''')
                c.execute('''CREATE TABLE IF NOT EXISTS fatwa_topics (
                    fatwa_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    PRIMARY KEY (fatwa_id, topic_id),
                    FOREIGN KEY (fatwa_id) REFERENCES fatwas(id),
                    FOREIGN KEY (topic_id) REFERENCES topics(id)
                )''')
                # Ø§Ù„ÙÙ‡Ø§Ø±Ø³ (Indexing)
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwa_number ON fatwas(fatwa_number)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_scholar_id ON fatwas(scholar_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_source_title_id ON fatwas(source_title_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_status ON fatwas(status)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_favorites_count ON fatwas(favorites_count)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_normalized_answer ON fatwas(normalized_answer)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_source_titles_source_id ON source_titles(source_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_topics_category_id ON topics(category_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwa_categories_category_id ON fatwa_categories(category_id)")
                c.execute("CREATE INDEX IF NOT EXISTS idx_fatwa_topics_topic_id ON fatwa_topics(topic_id)")
                # Full-Text Search (SQLite FTS5) for title/question/answer
                try:
                    c.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS fatwas_fts
                        USING fts5(title, question, answer, content='fatwas', content_rowid='id')
                    """)
                    c.execute("""
                        CREATE TRIGGER IF NOT EXISTS fatwas_ai AFTER INSERT ON fatwas BEGIN
                            INSERT INTO fatwas_fts(rowid, title, question, answer)
                            VALUES (new.id, new.title, new.question, new.answer);
                        END;
                    """)
                    c.execute("""
                        CREATE TRIGGER IF NOT EXISTS fatwas_ad AFTER DELETE ON fatwas BEGIN
                            INSERT INTO fatwas_fts(fatwas_fts, rowid, title, question, answer)
                            VALUES ('delete', old.id, old.title, old.question, old.answer);
                        END;
                    """)
                    c.execute("""
                        CREATE TRIGGER IF NOT EXISTS fatwas_au AFTER UPDATE ON fatwas BEGIN
                            INSERT INTO fatwas_fts(fatwas_fts, rowid, title, question, answer)
                            VALUES ('delete', old.id, old.title, old.question, old.answer);
                            INSERT INTO fatwas_fts(rowid, title, question, answer)
                            VALUES (new.id, new.title, new.question, new.answer);
                        END;
                    """)
                except Exception as e:
                    logger.warning(f"FTS setup skipped/failed: {e}")
                conn.commit()
                logger.info("Database initialized successfully with redesigned schema (sources + source titles + M2M taxonomy)")
            except Exception as e:
                logger.error(f"Database initialization error: {e}")
                raise
            finally:
                if conn:
                    conn.close()
        self.execute_with_retry(_init_db)
    # ---------------------------------------------------------
    # Helper Methods (Internal)
    # ---------------------------------------------------------

    def _get_or_create_scholar_id(self, cursor, scholar_name: str) -> Optional[int]:
        """Ø§Ù„Ø­ØµÙˆÙ„ Ø¹Ù„Ù‰ Ù…Ø¹Ø±Ù Ø§Ù„Ø¹Ø§Ù„Ù… Ø£Ùˆ Ø¥Ù†Ø´Ø§Ø¤Ù‡ Ø¥Ø°Ø§ Ù„Ù… ÙŠÙˆØ¬Ø¯"""
        if not scholar_name:
            return None
        cursor.execute("SELECT id FROM scholars WHERE name = ?", (scholar_name,))
        row = cursor.fetchone()
        if row:
            return row['id']
        else:
            cursor.execute("INSERT INTO scholars (name) VALUES (?)", (scholar_name,))
            return cursor.lastrowid

    def _get_or_create_source_id(self, cursor, name: str) -> Optional[int]:
        """Ø§Ù„Ø­ØµÙˆÙ„ Ø¹Ù„Ù‰ Ù…Ø¹Ø±Ù Ø§Ù„Ù…ØµØ¯Ø± Ø£Ùˆ Ø¥Ù†Ø´Ø§Ø¤Ù‡"""
        if name is None:
            return None
        name = str(name)
        cursor.execute("SELECT id FROM sources WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return row['id']
        cursor.execute("INSERT INTO sources (name) VALUES (?)", (name,))
        return cursor.lastrowid

    def _get_or_create_source_title_id(self, cursor, source_name: str, title: str, source_url: str = None, audio_url: str = None) -> Optional[int]:
        """Ø§Ù„Ø­ØµÙˆÙ„ Ø¹Ù„Ù‰ Ù…Ø¹Ø±Ù Ø¹Ù†ÙˆØ§Ù† Ø§Ù„Ù…ØµØ¯Ø± Ø£Ùˆ Ø¥Ù†Ø´Ø§Ø¤Ù‡"""
        if source_name is None:
            return None
        source_name = str(source_name)
        if title is None:
            title = ""

        source_id = self._get_or_create_source_id(cursor, source_name)
        if not source_id:
            return None

        # To avoid overwriting URLs across different fatwas (even with same title),
        # we only reuse a source_title if ALL fields match exactly.
        # Otherwise, we create a new entry.

        cursor.execute(
            "SELECT id FROM source_titles WHERE source_id = ? AND title = ? AND source_url IS ? AND audio_url IS ?",
            (source_id, title, source_url, audio_url)
        )
        row = cursor.fetchone()
        if row:
            return row['id']

        # Create new record if no exact match found
        cursor.execute(
            "INSERT INTO source_titles (source_id, title, source_url, audio_url) VALUES (?, ?, ?, ?)",
            (source_id, title, source_url, audio_url)
        )
        return cursor.lastrowid
    # ---------------------------------------------------------
    # Ø¹Ù…Ù„ÙŠØ§Øª Ø§Ù„ÙØªØ§ÙˆÙ‰ (Fatwas)
    # ---------------------------------------------------------

    def add_fatwa(self, data: Dict) -> int:
        """Ø¥Ø¶Ø§ÙØ© ÙØªÙˆÙ‰ Ø¬Ø¯ÙŠØ¯Ø© ÙˆÙÙ‚ Ø§Ù„Ù…Ø®Ø·Ø· Ø§Ù„Ø¬Ø¯ÙŠØ¯"""
        def _add_fatwa():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # 1. ØªØ­Ø¯ÙŠØ¯ Ø±Ù‚Ù… Ø§Ù„ÙØªÙˆÙ‰
                c.execute("SELECT MAX(fatwa_number) FROM fatwas")
                max_num = c.fetchone()[0]
                fatwa_num = (max_num or 0) + 1
                # 2. Ù…Ø¹Ø§Ù„Ø¬Ø© Ø§Ù„Ø¹Ø§Ù„Ù… (Scholar)
                scholar_name = data.get('scholar_name')
                scholar_id = self._get_or_create_scholar_id(c, scholar_name)
                # 3. Ù…Ø¹Ø§Ù„Ø¬Ø© Ø¹Ù†ÙˆØ§Ù† Ø§Ù„Ù…ØµØ¯Ø± (Source Title)
                source_name = data.get('source_name')
                source_title = data.get('source_title') or ""
                source_url = data.get('source_url')
                audio_url = data.get('audio_url')
                source_title_id = self._get_or_create_source_title_id(
                    c, source_name, source_title, source_url=source_url, audio_url=audio_url
                )
                if not source_title_id:
                    raise ValueError("Missing source name information for fatwa")
                # 4. Ø¥Ø¯Ø±Ø§Ø¬ Ø§Ù„ÙØªÙˆÙ‰
                answer_text = data.get('answer')
                question_text = data.get('question')
                c.execute(
                    '''INSERT INTO fatwas
                        (fatwa_number, title, question, answer, normalized_answer, status, views, scholar_id, source_title_id, created_at)
                        VALUES (?, ?, ?, ?, NORMALIZE_TEXT(?), ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                    (fatwa_num, data['title'], question_text, answer_text, answer_text, 'draft', 0, scholar_id, source_title_id)
                )
                fatwa_id = c.lastrowid
                # 5. Ø§Ù„ØªØµÙ†ÙŠÙØ§Øª ÙˆØ§Ù„Ù…ÙˆØ§Ø¶ÙŠØ¹ (Many-to-Many)
                classifications = data.get('classifications', [])
                category_ids = set()
                topic_ids = set()
                for cls in classifications:
                    if cls.get('category_id'):
                        category_ids.add(cls['category_id'])
                    for t_id in cls.get('topic_ids', []):
                        topic_ids.add(t_id)
                for cat_id in category_ids:
                    c.execute(
                        "INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) VALUES (?, ?)",
                        (fatwa_id, cat_id)
                    )
                for t_id in topic_ids:
                    c.execute(
                        "INSERT OR IGNORE INTO fatwa_topics (fatwa_id, topic_id) VALUES (?, ?)",
                        (fatwa_id, t_id)
                    )
                conn.commit()
                return fatwa_id
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_add_fatwa)

    def get_fatwa_by_number(self, fatwa_number: int) -> Optional[Dict]:
        return self.get_fatwa(fatwa_number=fatwa_number)

    def delete_fatwa(self, fatwa_id: int) -> bool:
        """Ø­Ø°Ù ÙØªÙˆÙ‰ Ù…Ù† Ù‚Ø§Ø¹Ø¯Ø© Ø§Ù„Ø¨ÙŠØ§Ù†Ø§Øª."""
        def _delete():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("DELETE FROM fatwa_topics WHERE fatwa_id = ?", (fatwa_id,))
                c.execute("DELETE FROM fatwa_categories WHERE fatwa_id = ?", (fatwa_id,))
                c.execute("DELETE FROM fatwas WHERE id = ?", (fatwa_id,))
                conn.commit()
                return c.rowcount > 0
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_delete)

    def get_all_fatwas(self, status: str = None, limit: int = 100, offset: int = 0) -> Tuple[List[Dict], int]:
        """Ø¬Ù„Ø¨ Ø§Ù„ÙƒÙ„ Ù…Ø¹ Ø§Ù„Ù‡ÙŠÙƒÙ„Ø© Ø§Ù„Ø¬Ø¯ÙŠØ¯Ø© (Pagination Supported)"""
        def _get_all():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()

                # Base Query
                where_clause = ""
                params = []
                if status:
                    where_clause = "WHERE f.status = ?"
                    params.append(status)

                # Count
                c.execute(f"SELECT COUNT(*) FROM fatwas f {where_clause}", params)
                total_count = c.fetchone()[0]

                # Data
                sql = f"SELECT id FROM fatwas f {where_clause} ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                results = []
                for row in c.fetchall():
                    fatwa_data = self.get_fatwa(row[0])
                    if fatwa_data:
                        results.append(fatwa_data)
                return results, total_count
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get_all)

    def get_fatwas_by_ids(self, fatwa_ids: List[int], public_only: bool = False) -> List[Dict]:
        def _get():
            if not fatwa_ids:
                return []
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                placeholders = ",".join(["?"] * len(fatwa_ids))
                sql = f"SELECT id FROM fatwas WHERE id IN ({placeholders})"
                params = list(fatwa_ids)
                if public_only:
                    sql += " AND status = 'published'"
                c.execute(sql, params)
                found_ids = [row[0] for row in c.fetchall()]
                id_set = set(found_ids)
                ordered_ids = [fid for fid in fatwa_ids if fid in id_set]
                results = []
                for fid in ordered_ids:
                    fatwa_data = self.get_fatwa(fid)
                    if fatwa_data:
                        results.append(fatwa_data)
                return results
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def count_fatwas_by_ids(self, fatwa_ids: List[int], public_only: bool = False) -> int:
        def _count():
            if not fatwa_ids:
                return 0
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                placeholders = ",".join(["?"] * len(fatwa_ids))
                sql = f"SELECT COUNT(*) FROM fatwas WHERE id IN ({placeholders})"
                params = list(fatwa_ids)
                if public_only:
                    sql += " AND status = 'published'"
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_count)

    def get_fatwas_count(self, status: str = None) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT COUNT(*) FROM fatwas"
                params = []
                if status:
                    sql += " WHERE status = ?"
                    params.append(status)
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                conn.close()
        return self.execute_with_retry(_count)

    def increment_views(self, fatwa_id: int):
        """Ø²ÙŠØ§Ø¯Ø© Ø¹Ø¯Ø¯ Ø§Ù„Ù…Ø´Ø§Ù‡Ø¯Ø§Øª (Non-blocking / Safe)"""
        def _inc():
            conn = None
            try:
                conn = self.get_connection()
                # Use immediate transaction or just ignore errors
                conn.execute("UPDATE fatwas SET views = views + 1 WHERE id = ?", (fatwa_id,))
                conn.commit()
            except Exception as e:
                # Log but don't fail the request
                logger.warning(f"Failed to increment views for {fatwa_id}: {e}")
            finally:
                if conn:
                    conn.close()
        # Do not use retry loop for this, just one attempt or fail silently
        try:
            _inc()
        except Exception as e:
            logger.debug(f"Silent increment_views fallback failed for {fatwa_id}: {e}")

    def increment_views_by(self, fatwa_id: int, delta: int):
        """Increment views by delta (non-blocking / safe)."""
        if not delta or delta <= 0:
            return
        def _inc():
            conn = None
            try:
                conn = self.get_connection()
                conn.execute("UPDATE fatwas SET views = views + ? WHERE id = ?", (int(delta), fatwa_id))
                conn.commit()
            except Exception as e:
                logger.warning(f"Failed to increment views for {fatwa_id} by {delta}: {e}")
            finally:
                if conn:
                    conn.close()
        try:
            _inc()
        except Exception as e:
            logger.debug(f"Silent increment_views_by fallback failed for {fatwa_id}: {e}")

    def increment_favorites_count(self, fatwa_id: int, delta: int):
        """Ø²ÙŠØ§Ø¯Ø©/ØªÙ‚Ù„ÙŠÙ„ Ø¹Ø¯Ø¯ Ø§Ù„Ù…ÙØ¶Ù„Ø© Ù…Ø¹ Ù…Ù†Ø¹ Ø§Ù„Ù‚ÙŠÙ… Ø§Ù„Ø³Ø§Ù„Ø¨Ø©"""
        def _inc():
            conn = None
            try:
                conn = self.get_connection()
                conn.execute(
                    """
                    UPDATE fatwas
                    SET favorites_count = CASE
                        WHEN favorites_count + ? < 0 THEN 0
                        ELSE favorites_count + ?
                    END
                    WHERE id = ?
                    """,
                    (delta, delta, fatwa_id),
                )
                conn.commit()
            finally:
                if conn:
                    conn.close()
        self.execute_with_retry(_inc)

    def get_top_favorites(self, limit: int = 5) -> List[Dict]:
        """Ø£ÙƒØ«Ø± Ø§Ù„ÙØªØ§ÙˆÙ‰ ØªÙØ¶ÙŠÙ„Ø§Ù‹ Ø­Ø³Ø¨ Ø§Ù„Ø¹Ø¯Ø§Ø¯"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    """
                    SELECT id, title, favorites_count as fav_count
                    FROM fatwas
                    WHERE favorites_count > 0
                    ORDER BY favorites_count DESC, fatwa_number DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)
    # ---------------------------------------------------------
    # Ø¹Ù…Ù„ÙŠØ§Øª Ø§Ù„Ø±ÙˆØ§Ø¨Ø· Ø§Ù„Ù†Ø§Ù‚ØµØ© (Missing Links)
    # ---------------------------------------------------------

    def add_category(self, name: str, description: str = None, category_type: str = 'fiqh') -> int:
        """Add category (new schema: no description column)."""
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("INSERT INTO categories (name, type) VALUES (?, ?)", (name, category_type))
                conn.commit()
                return c.lastrowid
            except sqlite3.IntegrityError:
                return 0
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_add)

    def update_category(self, cat_id: int, new_name: str = None, category_type: str = None) -> bool:
        """Update category name/type with basic merge support for duplicates."""
        def _update():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                if category_type and not new_name:
                    c.execute("UPDATE categories SET type = ? WHERE id = ?", (category_type, cat_id))
                    conn.commit()
                    return c.rowcount > 0
                if new_name:
                    c.execute("SELECT id FROM categories WHERE name = ? AND id != ?", (new_name, cat_id))
                    existing = c.fetchone()
                    if existing:
                        target_id = existing['id']
                        # Move fatwa-category links
                        c.execute(
                            "INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) "
                            "SELECT fatwa_id, ? FROM fatwa_categories WHERE category_id = ?",
                            (target_id, cat_id)
                        )
                        c.execute("DELETE FROM fatwa_categories WHERE category_id = ?", (cat_id,))
                        # Move topics to target category
                        c.execute("UPDATE topics SET category_id = ? WHERE category_id = ?", (target_id, cat_id))
                        # Delete old category
                        c.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
                        conn.commit()
                        return True
                    c.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, cat_id))
                    conn.commit()
                    return c.rowcount > 0
                return False
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_update)

    def delete_category(self, cat_id: int) -> bool:
        """Delete category and detach all related topics and fatwa links."""
        def _delete():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Remove fatwa-topic links for topics under this category
                c.execute(
                    "DELETE FROM fatwa_topics WHERE topic_id IN (SELECT id FROM topics WHERE category_id = ?)",
                    (cat_id,)
                )
                # Remove topics
                c.execute("DELETE FROM topics WHERE category_id = ?", (cat_id,))
                # Remove fatwa-category links
                c.execute("DELETE FROM fatwa_categories WHERE category_id = ?", (cat_id,))
                # Remove category
                c.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
                conn.commit()
                return c.rowcount > 0
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_delete)

    def delete_topic(self, topic_id: int) -> bool:
        """Delete topic and detach it from fatwas."""
        def _delete():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("DELETE FROM fatwa_topics WHERE topic_id = ?", (topic_id,))
                c.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
                conn.commit()
                return c.rowcount > 0
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_delete)

    def get_fatwa(self, fatwa_id: int = None, fatwa_number: int = None) -> Optional[Dict]:
        """Fetch a fatwa with scholar, source title, categories, and topics."""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                conditions = []
                params = []
                if fatwa_id is not None:
                    conditions.append("f.id = ?")
                    params.append(fatwa_id)
                elif fatwa_number is not None:
                    conditions.append("f.fatwa_number = ?")
                    params.append(fatwa_number)
                else:
                    return None
                c.execute(f"""
                    SELECT f.*,
                        s.name as scholar_name, s.biography as scholar_biography, s.website as scholar_website,
                        st.title as source_title, st.source_url as source_url, st.audio_url as audio_url,
                        src.name as source_name
                    FROM fatwas f
                    LEFT JOIN scholars s ON f.scholar_id = s.id
                    LEFT JOIN source_titles st ON f.source_title_id = st.id
                    LEFT JOIN sources src ON st.source_id = src.id
                    WHERE {' AND '.join(conditions)}
                """, params)
                row = c.fetchone()
                if not row:
                    return None
                fatwa = dict(row)
                # Source/title fields
                fatwa['source_title'] = fatwa.get('source_title')
                # Categories
                c.execute("""
                    SELECT c.id, c.name, c.type
                    FROM categories c
                    JOIN fatwa_categories fc ON c.id = fc.category_id
                    WHERE fc.fatwa_id = ?
                    ORDER BY c.type, c.name
                """, (fatwa['id'],))
                category_rows = c.fetchall()
                # Topics
                c.execute("""
                    SELECT t.id as topic_id, t.name as topic_name, t.category_id as category_id,
                        c.type as category_type, c.name as category_name
                    FROM fatwa_topics ft
                    JOIN topics t ON ft.topic_id = t.id
                    JOIN categories c ON t.category_id = c.id
                    WHERE ft.fatwa_id = ?
                    ORDER BY c.type, c.name, t.name
                """, (fatwa['id'],))
                topic_rows = c.fetchall()
                topics_by_category = {}
                for r in topic_rows:
                    topics_by_category.setdefault(r['category_id'], []).append({
                        'id': r['topic_id'],
                        'name': r['topic_name']
                    })
                classifications = []
                slots_data = {}
                for cat in category_rows:
                    slot_idx = 1 if cat['type'] == 'fiqh' else 2
                    slots_data.setdefault(slot_idx, {})
                    slots_data[slot_idx][cat['id']] = {
                        'id': cat['id'],
                        'name': cat['name'],
                        'topics': topics_by_category.get(cat['id'], [])
                    }
                for slot_idx in sorted(slots_data.keys()):
                    for cat_id in sorted(slots_data[slot_idx].keys()):
                        cat = slots_data[slot_idx][cat_id]
                        classifications.append({
                            'category_id': cat['id'],
                            'category_name': cat['name'],
                            'topic_ids': [t['id'] for t in cat['topics']],
                            'topic_names': [t['name'] for t in cat['topics']],
                            'slot_index': slot_idx
                        })
                fatwa['classifications'] = classifications
                return fatwa
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def merge_duplicate_categories(self) -> Dict[str, int]:
        """Merge categories with the same name and type."""
        def _merge():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Find duplicates: name, type having count > 1
                c.execute("""
                    SELECT name, type, COUNT(id) as cnt, MIN(id) as keep_id
                    FROM categories
                    GROUP BY name, type
                    HAVING cnt > 1
                """)
                duplicates = c.fetchall()
                merged_count = 0
                for row in duplicates:
                    name = row['name']
                    ctype = row['type']
                    keep_id = row['keep_id']

                    # Get IDs to merge (all except keep_id)
                    c.execute("SELECT id FROM categories WHERE name = ? AND type = ? AND id != ?", (name, ctype, keep_id))
                    dup_ids = [r['id'] for r in c.fetchall()]

                    for dup_id in dup_ids:
                        # 1. Move fatwa links
                        # INSERT OR IGNORE avoids duplicates in the junction table if the fatwa is already linked to the target category
                        c.execute(
                            "INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) "
                            "SELECT fatwa_id, ? FROM fatwa_categories WHERE category_id = ?",
                            (keep_id, dup_id)
                        )
                        # Delete old links
                        c.execute("DELETE FROM fatwa_categories WHERE category_id = ?", (dup_id,))

                        # 2. Move topics
                        c.execute("UPDATE topics SET category_id = ? WHERE category_id = ?", (keep_id, dup_id))

                        # 3. Delete the duplicate category
                        c.execute("DELETE FROM categories WHERE id = ?", (dup_id,))
                        merged_count += 1

                conn.commit()
                return {"categories_merged": merged_count}
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_merge)

    def merge_duplicate_topics(self) -> Dict[str, int]:
        """Merge topics with the same name within the same category."""
        def _merge():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Find duplicates: name, category_id having count > 1
                c.execute("""
                    SELECT name, category_id, COUNT(id) as cnt, MIN(id) as keep_id
                    FROM topics
                    GROUP BY name, category_id
                    HAVING cnt > 1
                """)
                duplicates = c.fetchall()
                merged_count = 0
                for row in duplicates:
                    name = row['name']
                    cat_id = row['category_id']
                    keep_id = row['keep_id']

                    # Get IDs to merge
                    c.execute("SELECT id FROM topics WHERE name = ? AND category_id = ? AND id != ?", (name, cat_id, keep_id))
                    dup_ids = [r['id'] for r in c.fetchall()]

                    for dup_id in dup_ids:
                        # 1. Move fatwa links
                        c.execute(
                            "INSERT OR IGNORE INTO fatwa_topics (fatwa_id, topic_id) "
                            "SELECT fatwa_id, ? FROM fatwa_topics WHERE topic_id = ?",
                            (keep_id, dup_id)
                        )
                        # Delete old links
                        c.execute("DELETE FROM fatwa_topics WHERE topic_id = ?", (dup_id,))

                        # 2. Delete duplicate topic
                        c.execute("DELETE FROM topics WHERE id = ?", (dup_id,))
                        merged_count += 1

                conn.commit()
                return {"topics_merged": merged_count}
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_merge)

    def update_fatwa(self, fatwa_id: int, data: Dict) -> bool:
        """Update fatwa core fields and relations for new schema."""
        def _update():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                fields_map = {
                    'answer': 'answer',
                    'title': 'title',
                    'question': 'question',
                    'status': 'status',
                    'fatwa_number': 'fatwa_number',
                }
                update_clauses = []
                values = []
                if 'scholar_name' in data:
                    scholar_id = self._get_or_create_scholar_id(c, data['scholar_name'])
                    update_clauses.append("scholar_id = ?")
                    values.append(scholar_id)
                if data.get('source_title_id'):
                    update_clauses.append("source_title_id = ?")
                    values.append(data['source_title_id'])
                else:
                    if any(k in data for k in ['source_name', 'source_title', 'source_url', 'audio_url']):
                        source_name = data.get('source_name')
                        source_title = data.get('source_title')
                        source_url = data.get('source_url')
                        audio_url = data.get('audio_url')
                        # Fill missing parts from current record
                        if source_name is None or source_title is None or source_url is None or audio_url is None:
                            c.execute("""
                                SELECT src.name as source_name, st.title as source_title, st.source_url, st.audio_url
                                FROM fatwas f
                                JOIN source_titles st ON f.source_title_id = st.id
                                JOIN sources src ON st.source_id = src.id
                                WHERE f.id = ?
                            """, (fatwa_id,))
                            current = c.fetchone()
                            if current:
                                if source_name is None:
                                    source_name = current['source_name']
                                if source_title is None:
                                    source_title = current['source_title']
                                if source_url is None:
                                    source_url = current['source_url']
                                if audio_url is None:
                                    audio_url = current['audio_url']
                        if source_name is not None and source_title is not None:
                            st_id = self._get_or_create_source_title_id(
                                c, source_name, source_title, source_url=source_url, audio_url=audio_url
                            )
                            if st_id:
                                update_clauses.append("source_title_id = ?")
                                values.append(st_id)
                for key, val in data.items():
                    if key in fields_map:
                        if key == 'answer':
                            update_clauses.append("answer = ?")
                            values.append(val)
                            update_clauses.append("normalized_answer = NORMALIZE_TEXT(?)")
                            values.append(val if val is not None else "")
                        else:
                            update_clauses.append(f"{fields_map[key]} = ?")
                            values.append(val)
                if update_clauses:
                    values.append(fatwa_id)
                    sql = f"UPDATE fatwas SET {', '.join(update_clauses)} WHERE id = ?"
                    c.execute(sql, values)
                if 'classifications' in data:
                    c.execute("DELETE FROM fatwa_categories WHERE fatwa_id = ?", (fatwa_id,))
                    c.execute("DELETE FROM fatwa_topics WHERE fatwa_id = ?", (fatwa_id,))
                    classifications = data['classifications']
                    category_ids = set()
                    topic_ids = set()
                    for cls in classifications:
                        if cls.get('category_id'):
                            category_ids.add(cls['category_id'])
                        for t_id in cls.get('topic_ids', []):
                            topic_ids.add(t_id)
                    for cat_id in category_ids:
                        c.execute(
                            "INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) VALUES (?, ?)",
                            (fatwa_id, cat_id)
                        )
                    for t_id in topic_ids:
                        c.execute(
                            "INSERT OR IGNORE INTO fatwa_topics (fatwa_id, topic_id) VALUES (?, ?)",
                            (fatwa_id, t_id)
                        )
                conn.commit()
                return True
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_update)

    def search_fatwas(self, query_text: str = None, scholar: str = None, category_id: int = None, topic_id: int = None,
                limit: int = 50, offset: int = 0, public_only: bool = True, scope: str = 'all') -> Tuple[List[Dict], int]:
        """Search fatwas across text, scholar, source, titles, categories, and topics. Returns (results, total_count)."""
        def _search():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Build WHERE clauses with optional FTS; fallback to LIKE-only on FTS errors
                fts_available = False
                if query_text and scope != 'title':
                    try:
                        c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'")
                        fts_available = c.fetchone() is not None
                    except Exception as e:
                        logger.debug(f"FTS availability check failed in search_fatwas: {e}")
                        fts_available = False

                def _build_where_and_params(include_fts: bool):
                    params = []
                    where_clauses = []
                    if public_only:
                        where_clauses.append("f.status = 'published'")
                    if scholar:
                        where_clauses.append("REMOVE_TASHKEEL(s.name) LIKE ?")
                        params.append(f"%{scholar}%")
                    if category_id:
                        where_clauses.append("EXISTS (SELECT 1 FROM fatwa_categories fc2 WHERE fc2.fatwa_id = f.id AND fc2.category_id = ?)")
                        params.append(category_id)
                    if topic_id:
                        where_clauses.append("EXISTS (SELECT 1 FROM fatwa_topics ft2 WHERE ft2.fatwa_id = f.id AND ft2.topic_id = ?)")
                        params.append(topic_id)
                    if query_text:
                        text_clauses = []
                        if scope == 'title':
                            text_clauses.append("REMOVE_TASHKEEL(f.title) LIKE ?")
                            params.append(f"%{query_text}%")
                        else:
                            if include_fts and fts_available:
                                text_clauses.append("f.id IN (SELECT rowid FROM fatwas_fts WHERE fatwas_fts MATCH ?)")
                                params.append(query_text)
                            text_clauses.append("(REMOVE_TASHKEEL(f.title) LIKE ? OR REMOVE_TASHKEEL(f.question) LIKE ? OR REMOVE_TASHKEEL(f.answer) LIKE ?)")
                            params.extend([f"%{query_text}%"] * 3)
                            text_clauses.append("REMOVE_TASHKEEL(s.name) LIKE ?")
                            params.append(f"%{query_text}%")
                            text_clauses.append("REMOVE_TASHKEEL(src.name) LIKE ?")
                            params.append(f"%{query_text}%")
                            text_clauses.append("REMOVE_TASHKEEL(st.title) LIKE ?")
                            params.append(f"%{query_text}%")
                            text_clauses.append("REMOVE_TASHKEEL(c.name) LIKE ?")
                            params.append(f"%{query_text}%")
                            text_clauses.append("REMOVE_TASHKEEL(t.name) LIKE ?")
                            params.append(f"%{query_text}%")
                        where_clauses.append("(" + " OR ".join(text_clauses) + ")")
                    return where_clauses, params

                where_clauses, params = _build_where_and_params(include_fts=fts_available)

                base_sql = """
                    SELECT f.id
                    FROM fatwas f
                    LEFT JOIN scholars s ON f.scholar_id = s.id
                    LEFT JOIN source_titles st ON f.source_title_id = st.id
                    LEFT JOIN sources src ON st.source_id = src.id
                    LEFT JOIN fatwa_categories fc ON f.id = fc.fatwa_id
                    LEFT JOIN categories c ON fc.category_id = c.id
                    LEFT JOIN fatwa_topics ft ON f.id = ft.fatwa_id
                    LEFT JOIN topics t ON ft.topic_id = t.id
                """

                def _run_queries(where_clauses_run, params_run):
                    where_sql_run = ""
                    if where_clauses_run:
                        where_sql_run = " WHERE " + " AND ".join(where_clauses_run)

                    # Count Query
                    count_sql = f"SELECT COUNT(DISTINCT f.id) FROM fatwas f LEFT JOIN scholars s ON f.scholar_id = s.id LEFT JOIN source_titles st ON f.source_title_id = st.id LEFT JOIN sources src ON st.source_id = src.id LEFT JOIN fatwa_categories fc ON f.id = fc.fatwa_id LEFT JOIN categories c ON fc.category_id = c.id LEFT JOIN fatwa_topics ft ON f.id = ft.fatwa_id LEFT JOIN topics t ON ft.topic_id = t.id {where_sql_run}"
                    c.execute(count_sql, params_run)
                    total_count = c.fetchone()[0]

                    # Data Query
                    final_sql = f"{base_sql} {where_sql_run} GROUP BY f.id ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?"
                    data_params = list(params_run) + [limit, offset]
                    c.execute(final_sql, data_params)
                    results = []
                    for row in c.fetchall():
                        fatwa_data = self.get_fatwa(row[0])
                        if fatwa_data:
                            results.append(fatwa_data)
                    return results, total_count

                try:
                    results, total_count = _run_queries(where_clauses, params)
                except sqlite3.OperationalError as e:
                    # Fallback: remove FTS clause if it caused a syntax error
                    if fts_available and ("fts5" in str(e).lower() or "match" in str(e).lower() or "syntax" in str(e).lower() or "malformed" in str(e).lower()):
                        where_clauses_no_fts, params_no_fts = _build_where_and_params(include_fts=False)
                        results, total_count = _run_queries(where_clauses_no_fts, params_no_fts)
                    else:
                        raise

                return results, total_count
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_search)

    def get_related_fatwas(self, fatwa_id: int, limit: int = 5, public_only: bool = True) -> List[Dict]:
        """Get related fatwas by similarity order: title -> question -> answer -> same topic."""
        fatwa = self.get_fatwa(fatwa_id)
        if not fatwa:
            return []

        title_text = (fatwa.get('title') or "").strip()
        question_text = (fatwa.get('question') or "").strip()
        answer_text = (fatwa.get('answer') or "").strip()

        topic_ids: List[int] = []
        category_ids: List[int] = []
        for cls in fatwa.get('classifications', []):
            cid = cls.get('category_id')
            if cid:
                category_ids.append(int(cid))
            for tid in cls.get('topic_ids', []) or []:
                if tid:
                    topic_ids.append(int(tid))

        def _dedupe(items: List[int]) -> List[int]:
            seen = set()
            out = []
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                out.append(item)
            return out

        topic_ids = _dedupe(topic_ids)
        category_ids = _dedupe(category_ids)

        def _extract_terms(text: str, max_terms: int = 8) -> List[str]:
            if not text:
                return []
            import re
            from core.utils import remove_tashkeel
            cleaned = remove_tashkeel(text)
            cleaned = re.sub(r"[^\w\u0600-\u06FF]+", " ", cleaned, flags=re.UNICODE)
            tokens = [t.strip() for t in cleaned.split() if len(t.strip()) >= 3]
            terms = []
            for token in tokens:
                if token not in terms:
                    terms.append(token)
                if len(terms) >= max_terms:
                    break
            return terms

        title_terms = _extract_terms(title_text)
        question_terms = _extract_terms(question_text)
        answer_terms = _extract_terms(answer_text)

        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                related_ids: List[int] = []
                related_set = set()

                fts_available = False
                try:
                    c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'")
                    fts_available = c.fetchone() is not None
                except Exception as e:
                    logger.debug(f"FTS availability check failed in get_related_fatwas: {e}")
                    fts_available = False

                def _extend_unique(ids: List[int]):
                    for fid in ids:
                        if fid in related_set or fid == fatwa_id:
                            continue
                        related_set.add(fid)
                        related_ids.append(fid)

                def _search_by_field(field_name: str, terms: List[str], remaining: int):
                    if remaining <= 0 or not terms:
                        return
                    exclude_ids = [fatwa_id] + related_ids
                    exclude_placeholders = ",".join(["?"] * len(exclude_ids))
                    where_clauses = [f"f.id NOT IN ({exclude_placeholders})"]
                    params = list(exclude_ids)
                    if public_only:
                        where_clauses.append("f.status = 'published'")

                    if fts_available:
                        try:
                            fts_query = " OR ".join([f"{field_name}:\"{t}\"" for t in terms])
                            where_sql = " AND ".join(where_clauses + ["fatwas_fts MATCH ?"])
                            sql = f"""
                                SELECT f.id
                                FROM fatwas f
                                JOIN fatwas_fts ON f.id = fatwas_fts.rowid
                                WHERE {where_sql}
                                ORDER BY bm25(fatwas_fts)
                                LIMIT ?
                            """
                            params_fts = params + [fts_query, remaining]
                            c.execute(sql, params_fts)
                            _extend_unique([row['id'] for row in c.fetchall()])
                        except sqlite3.OperationalError as e:
                            logger.debug(f"FTS related search fallback to LIKE (field={field_name}): {e}")

                    remaining = max(0, limit - len(related_ids))
                    if remaining > 0:
                        like_clauses = []
                        like_params = []
                        for term in terms:
                            like = f"%{term}%"
                            like_clauses.append(f"REMOVE_TASHKEEL(f.{field_name}) LIKE ?")
                            like_params.append(like)

                        if like_clauses:
                            where_sql = " AND ".join(where_clauses + ["(" + " OR ".join(like_clauses) + ")"])
                            sql = f"""
                                SELECT f.id
                                FROM fatwas f
                                WHERE {where_sql}
                                ORDER BY f.fatwa_number DESC
                                LIMIT ?
                            """
                            params_like = params + like_params + [remaining]
                            c.execute(sql, params_like)
                            _extend_unique([row['id'] for row in c.fetchall()])

                # 1) Title similarity
                _search_by_field("title", title_terms, limit)

                # 2) Question similarity
                remaining = max(0, limit - len(related_ids))
                _search_by_field("question", question_terms, remaining)

                # 3) Answer similarity
                remaining = max(0, limit - len(related_ids))
                _search_by_field("answer", answer_terms, remaining)

                # 4) Fallback: same topics (if still missing)
                remaining = max(0, limit - len(related_ids))
                if remaining > 0 and topic_ids:
                    placeholders = ",".join(["?"] * len(topic_ids))
                    exclude_ids = [fatwa_id] + related_ids
                    exclude_placeholders = ",".join(["?"] * len(exclude_ids))
                    params = list(topic_ids) + exclude_ids
                    where_clauses = [
                        f"f.id NOT IN ({exclude_placeholders})",
                        "ts.topic_score IS NOT NULL",
                    ]
                    if public_only:
                        where_clauses.append("f.status = 'published'")

                    sql = f"""
                        SELECT f.id, COALESCE(ts.topic_score, 0) AS topic_score
                        FROM fatwas f
                        LEFT JOIN (
                            SELECT fatwa_id, COUNT(DISTINCT topic_id) AS topic_score
                            FROM fatwa_topics
                            WHERE topic_id IN ({placeholders})
                            GROUP BY fatwa_id
                        ) ts ON f.id = ts.fatwa_id
                        WHERE {' AND '.join(where_clauses)}
                        ORDER BY topic_score DESC, f.fatwa_number DESC
                        LIMIT ?
                    """
                    params.append(remaining)
                    c.execute(sql, params)
                    _extend_unique([row['id'] for row in c.fetchall()])

                return self.get_fatwas_by_ids(related_ids[:limit], public_only=public_only)
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_get)

    def get_search_count(self, query_text: str = None, scholar: str = None, category_id: int = None, topic_id: int = None) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                params = []
                where_clauses = []
                if query_text:
                    text_clauses = []
                    try:
                        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fatwas_fts'")
                        if c.fetchone():
                            text_clauses.append("f.id IN (SELECT rowid FROM fatwas_fts WHERE fatwas_fts MATCH ?)")
                            params.append(query_text)
                    except Exception as e:
                        logger.debug(f"FTS count pre-check failed in get_search_count: {e}")
                    text_clauses.append("(REMOVE_TASHKEEL(f.title) LIKE ? OR REMOVE_TASHKEEL(f.question) LIKE ? OR REMOVE_TASHKEEL(f.answer) LIKE ?)")
                    params.extend([f"%{query_text}%"] * 3)
                    text_clauses.append("REMOVE_TASHKEEL(s.name) LIKE ?")
                    params.append(f"%{query_text}%")
                    text_clauses.append("REMOVE_TASHKEEL(src.name) LIKE ?")
                    params.append(f"%{query_text}%")
                    text_clauses.append("REMOVE_TASHKEEL(st.title) LIKE ?")
                    params.append(f"%{query_text}%")
                    text_clauses.append("REMOVE_TASHKEEL(c.name) LIKE ?")
                    params.append(f"%{query_text}%")
                    text_clauses.append("REMOVE_TASHKEEL(t.name) LIKE ?")
                    params.append(f"%{query_text}%")
                    where_clauses.append("(" + " OR ".join(text_clauses) + ")")
                if scholar:
                    where_clauses.append("REMOVE_TASHKEEL(s.name) LIKE ?")
                    params.append(f"%{scholar}%")
                if category_id:
                    where_clauses.append("EXISTS (SELECT 1 FROM fatwa_categories fc2 WHERE fc2.fatwa_id = f.id AND fc2.category_id = ?)")
                    params.append(category_id)
                if topic_id:
                    where_clauses.append("EXISTS (SELECT 1 FROM fatwa_topics ft2 WHERE ft2.fatwa_id = f.id AND ft2.topic_id = ?)")
                    params.append(topic_id)
                sql = """
                    SELECT COUNT(DISTINCT f.id)
                    FROM fatwas f
                    LEFT JOIN scholars s ON f.scholar_id = s.id
                    LEFT JOIN source_titles st ON f.source_title_id = st.id
                    LEFT JOIN sources src ON st.source_id = src.id
                    LEFT JOIN fatwa_categories fc ON f.id = fc.fatwa_id
                    LEFT JOIN categories c ON fc.category_id = c.id
                    LEFT JOIN fatwa_topics ft ON f.id = ft.fatwa_id
                    LEFT JOIN topics t ON ft.topic_id = t.id
                    WHERE f.status = 'published'
                """
                if where_clauses:
                    sql += " AND " + " AND ".join(where_clauses)
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_count)

    def get_fatwas_by_scholar(self, scholar_name: str, public_only: bool = False, limit: int = 50, offset: int = 0) -> Tuple[List[Dict], int]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()

                # Get Scholar ID
                c.execute("SELECT id FROM scholars WHERE name = ?", (scholar_name,))
                s_row = c.fetchone()
                if not s_row:
                    return [], 0
                s_id = s_row[0]

                where_clause = "WHERE scholar_id = ?"
                params = [s_id]
                if public_only:
                    where_clause += " AND status = 'published'"

                # Count
                c.execute(f"SELECT COUNT(*) FROM fatwas {where_clause}", params)
                total_count = c.fetchone()[0]

                sql = f"SELECT id FROM fatwas {where_clause} ORDER BY fatwa_number DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])

                c.execute(sql, params)
                results = []
                for row in c.fetchall():
                    fatwa_data = self.get_fatwa(row[0])
                    if fatwa_data:
                        results.append(fatwa_data)
                return results, total_count
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_random_published_fatwa(self, category_id: int = None, topic_ids: Optional[List[int]] = None) -> Optional[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                params = []
                where_clauses = ["f.status = 'published'", "(f.question IS NOT NULL AND f.question != '')", "(f.answer IS NOT NULL AND f.answer != '')"]
                if category_id:
                    where_clauses.append("EXISTS (SELECT 1 FROM fatwa_categories fc2 WHERE fc2.fatwa_id = f.id AND fc2.category_id = ?)")
                    params.append(category_id)
                normalized_topic_ids = []
                for topic_id in topic_ids or []:
                    try:
                        topic_int = int(topic_id)
                    except (TypeError, ValueError):
                        continue
                    if topic_int <= 0 or topic_int in normalized_topic_ids:
                        continue
                    normalized_topic_ids.append(topic_int)
                if normalized_topic_ids:
                    placeholders = ",".join(["?"] * len(normalized_topic_ids))
                    where_clauses.append(
                        f"EXISTS (SELECT 1 FROM fatwa_topics ft2 WHERE ft2.fatwa_id = f.id AND ft2.topic_id IN ({placeholders}))"
                    )
                    params.extend(normalized_topic_ids)
                # Keep audio requirement if available
                where_clauses.append("(st.audio_url IS NOT NULL AND st.audio_url != '')")
                sql = f"""
                    SELECT f.id
                    FROM fatwas f
                    JOIN source_titles st ON f.source_title_id = st.id
                    WHERE {" AND ".join(where_clauses)}
                    ORDER BY RANDOM()
                    LIMIT 1
                """
                c.execute(sql, params)
                row = c.fetchone()
                if row:
                    return self.get_fatwa(row[0])
                return None
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_random_fatwa(
        self,
        public_only: bool = True,
        excluded_fatwa_ids: Optional[List[int]] = None,
    ) -> Optional[Dict]:
        """Fetch a random fatwa for browsing, with optional visibility filtering."""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                params = []
                where_clauses = [
                    "(f.answer IS NOT NULL AND TRIM(f.answer) != '')"
                ]

                if public_only:
                    where_clauses.append("f.status = 'published'")

                excluded_ids = []
                for fatwa_id in excluded_fatwa_ids or []:
                    try:
                        fid = int(fatwa_id)
                    except (TypeError, ValueError):
                        continue
                    if fid > 0 and fid not in excluded_ids:
                        excluded_ids.append(fid)

                if excluded_ids:
                    placeholders = ",".join(["?"] * len(excluded_ids))
                    where_clauses.append(f"f.id NOT IN ({placeholders})")
                    params.extend(excluded_ids)

                sql = f"""
                    SELECT f.id
                    FROM fatwas f
                    WHERE {" AND ".join(where_clauses)}
                    ORDER BY RANDOM()
                    LIMIT 1
                """
                c.execute(sql, params)
                row = c.fetchone()
                if not row:
                    return None
                return self.get_fatwa(row[0])
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_fatwas_missing_link(self, link_type: str, limit: int = 50, offset: int = 0) -> List[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                if link_type == 'source':
                    where_sql = "(st.source_url IS NULL OR st.source_url = '')"
                elif link_type == 'audio':
                    where_sql = "(st.audio_url IS NULL OR st.audio_url = '')"
                else:
                    return []
                sql = f"""
                    SELECT f.id
                    FROM fatwas f
                    JOIN source_titles st ON f.source_title_id = st.id
                    WHERE {where_sql}
                    ORDER BY f.fatwa_number DESC
                    LIMIT ? OFFSET ?
                """
                c.execute(sql, (limit, offset))
                results = []
                for row in c.fetchall():
                    fatwa_data = self.get_fatwa(row[0])
                    if fatwa_data:
                        results.append(fatwa_data)
                return results
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_missing_link_count(self, link_type: str) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                if link_type == 'source':
                    where_sql = "(st.source_url IS NULL OR st.source_url = '')"
                elif link_type == 'audio':
                    where_sql = "(st.audio_url IS NULL OR st.audio_url = '')"
                else:
                    return 0
                c.execute(f"""
                    SELECT COUNT(*)
                    FROM fatwas f
                    JOIN source_titles st ON f.source_title_id = st.id
                    WHERE {where_sql}
                """)
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_count)

    def get_duplicate_fatwas(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Ø¬Ù„Ø¨ Ø§Ù„ÙØªØ§ÙˆÙ‰ Ø§Ù„ØªÙŠ Ù„Ù‡Ø§ Ù†ÙØ³ Ù†Øµ Ø§Ù„Ø¬ÙˆØ§Ø¨ (Ø§Ù„Ù…ÙƒØ±Ø±Ø©)"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Ù†Ø³ØªØ®Ø¯Ù… NORMALIZE_TEXT Ù„Ù„Ù…Ù‚Ø§Ø±Ù†Ø© Ø§Ù„Ù‚ÙˆÙŠØ© (ØªØªØ¬Ø§Ù‡Ù„ Ø§Ù„ØªØ´ÙƒÙŠÙ„ ÙˆØ§Ù„ØªØ±Ù‚ÙŠÙ… ÙˆØ§Ù„Ù…Ø³Ø§ÙØ§Øª)
                sql = """
                    WITH normalized AS (
                        SELECT id, normalized_answer
                        FROM fatwas
                        WHERE normalized_answer IS NOT NULL
                        AND TRIM(normalized_answer) != ''
                    ),
                    duplicate_groups AS (
                        SELECT normalized_answer
                        FROM normalized
                        GROUP BY normalized_answer
                        HAVING COUNT(*) > 1
                    )
                    SELECT n.id
                    FROM normalized n
                    JOIN duplicate_groups d
                    ON d.normalized_answer = n.normalized_answer
                    ORDER BY n.normalized_answer, n.id
                    LIMIT ? OFFSET ?
                """
                c.execute(sql, (limit, offset))
                fatwa_ids = [row['id'] for row in c.fetchall()]
                if not fatwa_ids:
                    return []
                return self.get_fatwas_by_ids(fatwa_ids)
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_duplicate_count(self) -> int:
        """Ø¹Ø¯Ø¯ Ø§Ù„ÙØªØ§ÙˆÙ‰ Ø§Ù„Ù…ÙƒØ±Ø±Ø©"""
        def _count():
            conn = self.get_connection()
            try:
                c = conn.cursor()
                sql = """
                    WITH normalized AS (
                        SELECT normalized_answer
                        FROM fatwas
                        WHERE normalized_answer IS NOT NULL
                        AND TRIM(normalized_answer) != ''
                    )
                    SELECT COALESCE(SUM(group_count), 0)
                    FROM (
                        SELECT COUNT(*) AS group_count
                        FROM normalized
                        GROUP BY normalized_answer
                        HAVING COUNT(*) > 1
                    ) dup
                """
                c.execute(sql)
                row = c.fetchone()
                return int(row[0] if row and row[0] is not None else 0)
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_count)

    def find_fatwas_by_exact_answer(self, answer_text: str, limit: int = 5) -> List[Dict]:
        """Ø§Ù„Ø¨Ø­Ø« Ø¹Ù† ÙØªØ§ÙˆÙ‰ Ù„Ù‡Ø§ Ù†ÙØ³ Ù†Øµ Ø§Ù„Ø¬ÙˆØ§Ø¨ Ø¨Ø¹Ø¯ Ø§Ù„ØªØ·Ø¨ÙŠØ¹."""
        def _find():
            if not answer_text or not str(answer_text).strip():
                return []

            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Fast path: rely on precomputed normalized_answer (indexed).
                c.execute(
                    """
                    SELECT id, fatwa_number, title, status
                    FROM fatwas
                    WHERE normalized_answer = NORMALIZE_TEXT(?)
                      AND normalized_answer IS NOT NULL
                      AND TRIM(normalized_answer) != ''
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (answer_text, int(limit))
                )
                rows = c.fetchall()

                # Correctness fallback: normalize the stored answer at query time.
                # This catches any stale/missing normalized_answer values.
                if not rows:
                    c.execute(
                        """
                        SELECT id, fatwa_number, title, status
                        FROM fatwas
                        WHERE answer IS NOT NULL
                          AND TRIM(answer) != ''
                          AND NORMALIZE_TEXT(answer) = NORMALIZE_TEXT(?)
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (answer_text, int(limit))
                    )
                    rows = c.fetchall()

                return [
                    {
                        'id': row['id'],
                        'fatwa_number': row['fatwa_number'],
                        'title': row['title'],
                        'status': row['status'],
                    }
                    for row in rows
                ]
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_find)

    def get_scholars(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        """Ø§Ø³ØªØ±Ø¬Ø§Ø¹ Ø§Ù„Ø¹Ù„Ù…Ø§Ø¡ (Ø§Ù„Ù…Ø¹Ø±Ù ÙˆØ§Ø³Ù… Ø§Ù„Ø¹Ø§Ù„Ù…) Ù…Ù† Ø¬Ø¯ÙˆÙ„ scholars"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT id, name FROM scholars"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [(r['id'], r['name']) for r in c.fetchall()]
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_scholars_count(self, search_query: str = None) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT COUNT(*) FROM scholars"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                conn.close()
        return self.execute_with_retry(_count)

    def get_scholars_with_ids(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Dict]:
        """Ø§Ø³ØªØ±Ø¬Ø§Ø¹ Ø§Ù„Ø¹Ù„Ù…Ø§Ø¡ Ù…Ø¹ Ø§Ù„Ù…Ø¹Ø±ÙØ§Øª Ù…Ù† Ø¬Ø¯ÙˆÙ„ scholars"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT id, name, biography, website FROM scholars"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [dict(r) for r in c.fetchall()]
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_scholar_by_id(self, scholar_id: int) -> Optional[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT id, name, biography, website FROM scholars WHERE id = ?", (scholar_id,))
                row = c.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def add_scholar(self, name: str) -> int:
        """Ø¥Ø¶Ø§ÙØ© Ø¹Ø§Ù„Ù… Ø¬Ø¯ÙŠØ¯ Ø£Ùˆ Ø¥Ø±Ø¬Ø§Ø¹ Ø§Ù„Ù…Ø¹Ø±Ù Ø¥Ø°Ø§ ÙƒØ§Ù† Ù…ÙˆØ¬ÙˆØ¯Ø§Ù‹"""
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT id FROM scholars WHERE name = ?", (name,))
                row = c.fetchone()
                if row:
                    return row["id"]
                c.execute("INSERT INTO scholars (name) VALUES (?)", (name,))
                conn.commit()
                return c.lastrowid
            finally:
                conn.close()
        return self.execute_with_retry(_add)

    def update_scholar_bio_website(self, scholar_id: int, biography: str, website: str) -> bool:
        """ØªØ­Ø¯ÙŠØ« Ø³ÙŠØ±Ø© Ø§Ù„Ø¹Ø§Ù„Ù… ÙˆØ±Ø§Ø¨Ø· Ù…ÙˆÙ‚Ø¹Ù‡ Ø§Ù„Ø±Ø³Ù…ÙŠ"""
        def _update():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    "UPDATE scholars SET biography = ?, website = ? WHERE id = ?",
                    (biography, website, scholar_id)
                )
                conn.commit()
                return c.rowcount > 0
            finally:
                conn.close()
        return self.execute_with_retry(_update)

    # ---------------------------------------------------------
    # Sources Management
    # ---------------------------------------------------------

    def get_sources(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT id, name FROM sources WHERE 1=1"
                params = []
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [(r["id"], r["name"]) for r in c.fetchall()]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_fatwas_by_source(self, source_id: int, public_only: bool = False, limit: int = 50, offset: int = 0) -> Tuple[List[Dict], int]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()

                where_clause = "WHERE st.source_id = ?"
                params = [source_id]
                if public_only:
                    where_clause += " AND f.status = 'published'"

                # Count
                c.execute(f"""
                    SELECT COUNT(f.id)
                    FROM fatwas f
                    JOIN source_titles st ON f.source_title_id = st.id
                    {where_clause}
                """, params)
                total_count = c.fetchone()[0]

                # Data
                sql = f"""
                    SELECT f.id
                    FROM fatwas f
                    JOIN source_titles st ON f.source_title_id = st.id
                    {where_clause}
                    ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?
                """
                params.extend([limit, offset])

                c.execute(sql, params)
                results = []
                for row in c.fetchall():
                    fatwa_data = self.get_fatwa(row[0])
                    if fatwa_data:
                        results.append(fatwa_data)
                return results, total_count
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_sources_count(self, search_query: str = None) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT COUNT(*) FROM sources WHERE 1=1"
                params = []
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_count)

    def get_source(self, source_id: int) -> Optional[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT id, name FROM sources WHERE id = ?", (source_id,))
                row = c.fetchone()
                return dict(row) if row else None
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def add_source(self, name: str) -> int:
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT id FROM sources WHERE name = ?", (name,))
                row = c.fetchone()
                if row:
                    return row["id"]
                c.execute("INSERT INTO sources (name) VALUES (?)", (name,))
                conn.commit()
                return c.lastrowid
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_add)


    def merge_sources(self, source_id: int, target_source_name: str) -> bool:
        """Merge source_id into a source with target_source_name.
           Moves all source_titles from source_id to target source.
           Deletes source_id.
        """
        def _merge():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()

                # 1. Get Target Source ID
                c.execute("SELECT id FROM sources WHERE name = ?", (target_source_name,))
                target_row = c.fetchone()
                if not target_row:
                    return False # Target source must exist for a merge to happen via this method logic
                target_source_id = target_row['id']

                if target_source_id == source_id:
                    return False # Cannot merge into self

                # 2. Move source_titles to target_source_id
                # We need to handle potential unique constraint violations if (source_id, title) combo already exists?
                # The schema for source_titles is:
                # CREATE TABLE IF NOT EXISTS source_titles (
                #     id INTEGER PRIMARY KEY AUTOINCREMENT,
                #     source_id INTEGER NOT NULL,
                #     title TEXT NOT NULL,
                #     source_url TEXT,
                #     audio_url TEXT,
                #     FOREIGN KEY (source_id) REFERENCES sources(id)
                # )
                # There is no UNIQUE constraint on (source_id, title) in the schema shown in lines 87-94.
                # So we can just update source_id.

                c.execute("UPDATE source_titles SET source_id = ? WHERE source_id = ?", (target_source_id, source_id))

                # 3. Delete old source
                c.execute("DELETE FROM sources WHERE id = ?", (source_id,))

                conn.commit()
                return True
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_merge)

    def update_source(self, source_id: int, new_name: str) -> bool:
        def _update():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("UPDATE sources SET name = ? WHERE id = ?", (new_name, source_id))
                conn.commit()
                return c.rowcount > 0
            except sqlite3.IntegrityError:
                return False
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_update)

    def get_source_titles_count(self, source_id: int) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM source_titles WHERE source_id = ?", (source_id,))
                return c.fetchone()[0]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_count)

    def delete_source(self, source_id: int) -> bool:
        def _delete():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM source_titles WHERE source_id = ?", (source_id,))
                if c.fetchone()[0] > 0:
                    return False
                c.execute("DELETE FROM sources WHERE id = ?", (source_id,))
                conn.commit()
                return c.rowcount > 0
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_delete)

    def get_category(self, cat_id: int) -> Optional[Dict]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT id, name FROM categories WHERE id = ?", (cat_id,))
                row = c.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_categories(self, limit: int = None, offset: int = 0, search_query: str = None, category_type: str = None) -> List[Tuple[int, str]]:
        """Ø¬Ù„Ø¨ Ø§Ù„ØªØµÙ†ÙŠÙØ§Øª Ù…Ø¹ Ø¯Ø¹Ù… Ø§Ù„Ø¨Ø­Ø« ÙˆØ§Ù„ÙÙ„ØªØ±Ø© Ø¨Ø§Ù„Ù†ÙˆØ¹"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT id, name FROM categories WHERE 1=1"
                params = []
                if category_type:
                    sql += " AND type = ?"
                    params.append(category_type)
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [(r['id'], r['name']) for r in c.fetchall()]
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_categories_count(self, search_query: str = None, category_type: str = None) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT COUNT(*) FROM categories WHERE 1=1"
                params = []
                if category_type:
                    sql += " AND type = ?"
                    params.append(category_type)
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                conn.close()
        return self.execute_with_retry(_count)

    def get_topics(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT id, name FROM topics"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [(r['id'], r['name']) for r in c.fetchall()]
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_topics_by_category(self, category_id: int, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT id, name FROM topics WHERE category_id = ?"
                params = [category_id]
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                c.execute(sql, params)
                return [(r['id'], r['name']) for r in c.fetchall()]
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_topics_count(self, category_id: int, search_query: str = None) -> int:
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                sql = "SELECT COUNT(*) FROM topics WHERE category_id = ?"
                params = [category_id]
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                c.execute(sql, params)
                return c.fetchone()[0]
            finally:
                conn.close()
        return self.execute_with_retry(_count)

    def add_topic(self, name: str, category_id: int = None) -> int:
        """Ø¥Ø¶Ø§ÙØ© Ù…ÙˆØ¶ÙˆØ¹ Ø¬Ø¯ÙŠØ¯ Ù…Ø¹ Ø±Ø¨Ø·Ù‡ Ø¨ØªØµÙ†ÙŠÙ"""
        def _add():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                # Ø§Ù„ØªØ­Ù‚Ù‚ Ø¥Ø°Ø§ ÙƒØ§Ù† Ù…ÙˆØ¬ÙˆØ¯Ø§Ù‹ Ù…Ø³Ø¨Ù‚Ø§Ù‹ ÙÙŠ Ù†ÙØ³ Ø§Ù„ØªØµÙ†ÙŠÙ
                c.execute("SELECT id FROM topics WHERE name = ? AND category_id = ?", (name, category_id))
                existing = c.fetchone()
                if existing:
                    return existing['id']
                # Ù…Ø­Ø§ÙˆÙ„Ø© Ø§Ù„Ø¥Ø¶Ø§ÙØ©
                c.execute("INSERT INTO topics (name, category_id) VALUES (?, ?)", (name, category_id))
                conn.commit()
                return c.lastrowid
            except sqlite3.IntegrityError:
                # Ø¥Ø°Ø§ ÙØ´Ù„ Ø¨Ø³Ø¨Ø¨ UNIQUE(name) ÙÙŠ Ø§Ù„Ù†Ø³Ø® Ø§Ù„Ù‚Ø¯ÙŠÙ…Ø© Ù…Ù† Ø§Ù„Ø¯Ø§ØªØ§ØŒ Ù†Ø­Ø§ÙˆÙ„ Ø¬Ù„Ø¨ Ø§Ù„Ù…ÙˆØ¬ÙˆØ¯
                if category_id is not None:
                    c.execute("SELECT id FROM topics WHERE name = ? AND category_id = ?", (name, category_id))
                else:
                    c.execute("SELECT id FROM topics WHERE name = ?", (name,))
                ex = c.fetchone()
                return ex['id'] if ex else 0
            finally:
                conn.close()
        return self.execute_with_retry(_add)

    def get_topic_id_by_name(self, name: str, category_id: Optional[int] = None) -> Optional[int]:
        """Ø§Ù„Ø­ØµÙˆÙ„ Ø¹Ù„Ù‰ Ù…Ø¹Ø±Ù Ø§Ù„Ù…ÙˆØ¶ÙˆØ¹ Ø¨ÙˆØ§Ø³Ø·Ø© Ø§Ù„Ø§Ø³Ù…"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                if category_id is not None:
                    c.execute("SELECT id FROM topics WHERE name = ? AND category_id = ?", (name, category_id))
                else:
                    c.execute("SELECT id FROM topics WHERE name = ?", (name,))
                row = c.fetchone()
                return row['id'] if row else None
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def update_topic(self, topic_id: int, new_name: str) -> bool:
        """ØªØ­Ø¯ÙŠØ« Ø§Ø³Ù… Ø§Ù„Ù…ÙˆØ¶ÙˆØ¹"""
        def _update():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("UPDATE topics SET name = ? WHERE id = ?", (new_name, topic_id))
                conn.commit()
                return c.rowcount > 0
            except sqlite3.IntegrityError:
                return False
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_update)

    def get_statistics(self) -> Dict:
        def _stats():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                stats = {}
                c.execute("SELECT COUNT(*) as count FROM fatwas")
                stats['total_fatwas'] = c.fetchone()['count']
                c.execute("SELECT COUNT(*) as count FROM fatwas WHERE status='published'")
                stats['published_fatwas'] = c.fetchone()['count']
                c.execute("SELECT COUNT(*) as count FROM fatwas WHERE status='draft'")
                stats['draft_fatwas'] = c.fetchone()['count']
                c.execute("SELECT COUNT(*) as count FROM categories")
                stats['categories'] = c.fetchone()['count']
                c.execute("SELECT COUNT(*) as count FROM scholars")
                stats['scholars'] = c.fetchone()['count']
                c.execute("SELECT SUM(views) as views FROM fatwas")
                res = c.fetchone()['views']
                stats['total_views'] = res if res else 0
                return stats
            finally:
                conn.close()
        return self.execute_with_retry(_stats)

    def count_fatwas_since(self, since_ts: str) -> int:
        """Count fatwas added since a specific timestamp (YYYY-MM-DD HH:MM:SS)."""
        def _count():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    "SELECT COUNT(*) as count FROM fatwas WHERE datetime(created_at) >= datetime(?)",
                    (since_ts,),
                )
                row = c.fetchone()
                return int(row["count"] if row else 0)
            finally:
                if conn:
                    conn.close()

        return self.execute_with_retry(_count)

    def backfill_created_at(self) -> int:
        """Backfill created_at for existing rows that are missing it."""
        def _backfill():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("PRAGMA table_info(fatwas)")
                columns = [row['name'] for row in c.fetchall()]
                if 'created_at' not in columns:
                    return 0
                c.execute("UPDATE fatwas SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at = ''")
                conn.commit()
                return c.rowcount or 0
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_backfill)

    def get_new_fatwa_counts_by_scholar_since(self, since_ts: str) -> List[Dict]:
        """Get counts of newly added published fatwas per scholar since a timestamp (UTC)."""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute(
                    """
                    SELECT s.name as scholar_name, COUNT(*) as count
                    FROM fatwas f
                    JOIN scholars s ON f.scholar_id = s.id
                    WHERE f.status = 'published'
                      AND f.created_at >= ?
                    GROUP BY f.scholar_id
                    ORDER BY count DESC, s.name ASC
                    """,
                    (since_ts,),
                )
                return [dict(row) for row in c.fetchall()]
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)

    def get_total_views(self) -> int:
        return self.get_statistics()['total_views']

    def backup_database(self, backup_path: str) -> bool:
        """Create a backup of the SQLite database"""
        try:
            # Check if source exists
            if not os.path.exists(self.db_name):
                return False
            src_conn = self.get_connection()
            dst_conn = sqlite3.connect(backup_path, timeout=30.0)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            return True
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return False

    def export_json(self, json_path: str) -> bool:
        """Export database to JSON"""
        import json
        try:
            fatwas, _ = self.get_all_fatwas(limit=10000) # Fetch all
            # Convert row objects to dicts if needed (get_all_fatwas returns dicts based on earlier code)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(fatwas, f, ensure_ascii=False, indent=2, default=str)
            return True
        except Exception as e:
            logger.error(f"JSON Export failed: {e}")
            return False

    def get_category_by_id(self, category_id: int) -> Optional[Dict]:
        """Ø¬Ù„Ø¨ Ø¨ÙŠØ§Ù†Ø§Øª ØªØµÙ†ÙŠÙ ÙˆØ§Ø­Ø¯"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM categories WHERE id = ?", (category_id,))
                row = c.fetchone()
                return dict(row) if row else None
            finally:
                conn.close()
        return self.execute_with_retry(_get)

    def get_topic(self, topic_id: int) -> Optional[Dict]:
        """Ø¬Ù„Ø¨ Ø¨ÙŠØ§Ù†Ø§Øª Ù…ÙˆØ¶ÙˆØ¹ ÙˆØ§Ø­Ø¯"""
        def _get():
            conn = None
            try:
                conn = self.get_connection()
                c = conn.cursor()
                c.execute("SELECT * FROM topics WHERE id = ?", (topic_id,))
                row = c.fetchone()
                return dict(row) if row else None
            finally:
                if conn:
                    conn.close()
        return self.execute_with_retry(_get)
