import sqlite3
import os
import logging
from datetime import datetime

# Configuration
TARGET_DB = r"d:\Projects\Bots\Fatwa_Cms_App\fatwa_cms_bot\data\bot_internal.db"
SOURCE_DBS = [
    {
        "name": "Titan_PDF_Bot",
        "path": r"D:\Projects\Bots\Titan_PDF_Bot\storage\database\titan_v8_ultimate.db",
        "user_query": "SELECT user_id, username, first_name, join_date, is_blocked FROM users",
        "channel_query": None # Doesn't seem to have channels
    },
    {
        "name": "TitanSv_bot",
        "path": r"D:\Projects\Bots\TitanSv_bot\data\users.db",
        "user_query": "SELECT user_id, username, full_name, join_date, is_banned FROM users",
        "channel_query": "SELECT chat_id, title, username, type, join_date FROM channels_groups"
    },
    {
        "name": "Fadhakir_bot",
        "path": r"D:\Projects\Bots\Fadhakir_bot\data\Fadhakir_bot.db",
        "user_query": "SELECT user_id, username, full_name, created_at, 0 FROM users",
        "channel_query": "SELECT chat_id, title, username, chat_type, created_at FROM chats"
    }
]

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def migrate():
    if not os.path.exists(TARGET_DB):
        logger.error(f"Target DB not found: {TARGET_DB}")
        return

    target_conn = sqlite3.connect(TARGET_DB)
    target_cursor = target_conn.cursor()

    total_users_added = 0
    total_channels_added = 0

    for source in SOURCE_DBS:
        name = source["name"]
        path = source["path"]
        
        if not os.path.exists(path):
            logger.warning(f"Source DB for {name} not found at {path}. Skipping.")
            continue

        logger.info(f"Processing {name}...")
        source_conn = sqlite3.connect(path)
        source_cursor = source_conn.cursor()

        # Migrate Users
        if source["user_query"]:
            try:
                source_cursor.execute(source["user_query"])
                rows = source_cursor.fetchall()
                for row in rows:
                    user_id, username, name_val, joined, blocked = row
                    # Ensure user_id is integer
                    try:
                        user_id = int(user_id)
                    except:
                        continue
                    
                    target_cursor.execute("""
                        INSERT OR IGNORE INTO users (user_id, username, full_name, joined_at, is_blocked)
                        VALUES (?, ?, ?, ?, ?)
                    """, (user_id, username, name_val, joined, blocked))
                    if target_cursor.rowcount > 0:
                        total_users_added += 1
            except Exception as e:
                logger.error(f"Error migrating users from {name}: {e}")

        # Migrate Channels
        if source["channel_query"]:
            try:
                source_cursor.execute(source["channel_query"])
                rows = source_cursor.fetchall()
                for row in rows:
                    chat_id, title, username, chat_type, added_at = row
                    try:
                        chat_id = int(chat_id)
                    except:
                        continue
                    
                    # Target channels schema: chat_id, title, username, type, status, added_at
                    target_cursor.execute("""
                        INSERT OR IGNORE INTO channels (chat_id, title, username, type, status, added_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (chat_id, title, username, chat_type, 'active', added_at))
                    if target_cursor.rowcount > 0:
                        total_channels_added += 1
            except Exception as e:
                logger.error(f"Error migrating channels from {name}: {e}")

        source_conn.close()

    target_conn.commit()
    target_conn.close()

    logger.info("========================================")
    logger.info(f"Migration completed!")
    logger.info(f"Total new users added: {total_users_added}")
    logger.info(f"Total new channels added: {total_channels_added}")
    logger.info("========================================")

if __name__ == "__main__":
    migrate()
