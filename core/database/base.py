import aiosqlite
import asyncio
import logging
import os
from datetime import datetime
from core.config import DB_PATH

logger = logging.getLogger(__name__)

class DatabaseBase:
    """Base class for Database Manager with singleton and connection logic (Async)."""
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_name=DB_PATH, max_retries=3, retry_delay=1.0):
        self.db_name = db_name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # Note: init_db must be called explicitly now as it is async

    async def execute_with_retry(self, func, *args, **kwargs):
        """Execute an async database operation with retry logic for locked databases."""
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e) and attempt < self.max_retries - 1:
                    logger.warning(f"Database locked, retrying ({attempt + 1}/{self.max_retries})...")
                    await asyncio.sleep(self.retry_delay)
                    continue
                else:
                    raise e
            except Exception as e:
                logger.error(f"Database error: {e}")
                raise

    async def get_connection(self):
        """Returns an aiosqlite connection with proper configuration."""
        try:
            from core.utils import remove_tashkeel, normalize_text
            conn = await aiosqlite.connect(self.db_name, timeout=30.0)
            conn.row_factory = aiosqlite.Row
            try:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA synchronous=NORMAL")
                await conn.execute("PRAGMA foreign_keys=ON")
                await conn.execute("PRAGMA temp_store=MEMORY")
                await conn.execute("PRAGMA busy_timeout = 5000")
            except Exception as e:
                logger.warning(f"SQLite PRAGMA setup failed: {e}")
            
            # create_function is synchronous in aiosqlite's wrapper for some reason? 
            # Actually, it's a method on the connection.
            await conn.create_function("REMOVE_TASHKEEL", 1, remove_tashkeel)
            await conn.create_function("NORMALIZE_TEXT", 1, normalize_text)
            return conn
        except Exception as e:
            logger.error(f"Error connecting to database: {e}")
            raise

    async def init_db(self):
        """Initialize the database schema if it doesn't exist."""
        if DatabaseBase._initialized:
            return

        async def _init_db():
            conn = None
            try:
                conn = await self.get_connection()
                
                # 1. Scholars table
                await conn.execute('''CREATE TABLE IF NOT EXISTS scholars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    biography TEXT,
                    website TEXT
                )''')
                
                # 2. Sources table
                await conn.execute('''CREATE TABLE IF NOT EXISTS sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                )''')
                
                # 3. Source Titles table
                await conn.execute('''CREATE TABLE IF NOT EXISTS source_titles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT,
                    audio_url TEXT,
                    FOREIGN KEY (source_id) REFERENCES sources(id)
                )''')
                
                # 4. Categories table
                await conn.execute('''CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    type TEXT CHECK(type IN ('fiqh', 'topic')) NOT NULL
                )''')
                
                # 5. Topics table
                await conn.execute('''CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )''')
                
                # 6. Fatwas table
                await conn.execute('''CREATE TABLE IF NOT EXISTS fatwas (
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
                    async with conn.execute("PRAGMA table_info(fatwas)") as cursor:
                        columns = [row['name'] for row in await cursor.fetchall()]
                    
                    if 'favorites_count' not in columns:
                        await conn.execute("ALTER TABLE fatwas ADD COLUMN favorites_count INTEGER DEFAULT 0")
                        await conn.execute("UPDATE fatwas SET favorites_count = 0 WHERE favorites_count IS NULL")
                    
                    if 'created_at' not in columns:
                        await conn.execute("ALTER TABLE fatwas ADD COLUMN created_at TEXT")
                        await conn.execute("UPDATE fatwas SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at = ''")
                    
                    if 'normalized_answer' not in columns:
                        await conn.execute("ALTER TABLE fatwas ADD COLUMN normalized_answer TEXT")
                    
                    # Backfill normalized_answer if needed
                    async with conn.execute("""
                        SELECT 1 FROM fatwas 
                        WHERE (normalized_answer IS NULL OR normalized_answer = '') 
                        AND answer IS NOT NULL AND TRIM(answer) != '' LIMIT 1
                    """) as cursor:
                        if await cursor.fetchone():
                            await conn.execute("""
                                UPDATE fatwas SET normalized_answer = NORMALIZE_TEXT(answer)
                                WHERE (normalized_answer IS NULL OR normalized_answer = '')
                                AND answer IS NOT NULL AND TRIM(answer) != ''
                            """)
                except Exception as e:
                    logger.warning(f"Failed to check/update fatwas table columns: {e}")

                # 7. Junction Tables
                await conn.execute('''CREATE TABLE IF NOT EXISTS fatwa_categories (
                    fatwa_id INTEGER NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY (fatwa_id, category_id),
                    FOREIGN KEY (fatwa_id) REFERENCES fatwas(id),
                    FOREIGN KEY (category_id) REFERENCES categories(id)
                )''')
                await conn.execute('''CREATE TABLE IF NOT EXISTS fatwa_topics (
                    fatwa_id INTEGER NOT NULL,
                    topic_id INTEGER NOT NULL,
                    PRIMARY KEY (fatwa_id, topic_id),
                    FOREIGN KEY (fatwa_id) REFERENCES fatwas(id),
                    FOREIGN KEY (topic_id) REFERENCES topics(id)
                )''')

                # Indexes
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwa_number ON fatwas(fatwa_number)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_scholar_id ON fatwas(scholar_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_source_title_id ON fatwas(source_title_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_status ON fatwas(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_favorites_count ON fatwas(favorites_count)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwas_normalized_answer ON fatwas(normalized_answer)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_source_titles_source_id ON source_titles(source_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_topics_category_id ON topics(category_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwa_categories_category_id ON fatwa_categories(category_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_fatwa_topics_topic_id ON fatwa_topics(topic_id)")

                # FTS
                try:
                    await conn.execute("""
                        CREATE VIRTUAL TABLE IF NOT EXISTS fatwas_fts
                        USING fts5(title, question, answer, content='fatwas', content_rowid='id')
                    """)
                    await conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS fatwas_ai AFTER INSERT ON fatwas BEGIN
                            INSERT INTO fatwas_fts(rowid, title, question, answer)
                            VALUES (new.id, new.title, new.question, new.answer);
                        END;
                    """)
                    await conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS fatwas_ad AFTER DELETE ON fatwas BEGIN
                            INSERT INTO fatwas_fts(fatwas_fts, rowid, title, question, answer)
                            VALUES ('delete', old.id, old.title, old.question, old.answer);
                        END;
                    """)
                    await conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS fatwas_au AFTER UPDATE ON fatwas BEGIN
                            INSERT INTO fatwas_fts(fatwas_fts, rowid, title, question, answer)
                            VALUES ('delete', old.id, old.title, old.question, old.answer);
                            INSERT INTO fatwas_fts(rowid, title, question, answer)
                            VALUES (new.id, new.title, new.question, new.answer);
                        END;
                    """)
                except Exception as e:
                    logger.warning(f"FTS setup skipped/failed: {e}")

                await conn.commit()
                logger.info("Database initialized successfully.")
            except Exception as e:
                logger.error(f"Database initialization error: {e}")
                raise
            finally:
                if conn:
                    await conn.close()
        
        await self.execute_with_retry(_init_db)
        DatabaseBase._initialized = True

    async def vacuum(self):
        """Optimize the database using VACUUM."""
        async def _do_vacuum():
            conn = await self.get_connection()
            try:
                await conn.execute("VACUUM")
                logger.info(f"Database {self.db_name} VACUUM completed.")
            except Exception as e:
                logger.warning(f"VACUUM failed for {self.db_name}: {e}")
            finally:
                await conn.close()
        
        await self.execute_with_retry(_do_vacuum)
