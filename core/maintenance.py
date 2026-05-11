import os
import time
import logging
import shutil
from datetime import datetime, timedelta
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

async def periodic_maintenance_job(context: ContextTypes.DEFAULT_TYPE):
    """
    وظيفة دورية لصيانة البوت:
    1. تنظيف ملفات السجلات القديمة (أكبر من 30 يوم).
    2. تنظيف المجلدات المؤقتة.
    3. ضغط قاعدة البيانات (VACUUM).
    """
    logger.info("🧹 Starting periodic maintenance job...")
    
    # 1. Cleanup Logs
    log_dir = "logs"
    if os.path.exists(log_dir):
        now = time.time()
        for f in os.listdir(log_dir):
            fpath = os.path.join(log_dir, f)
            if os.stat(fpath).st_mtime < now - (30 * 86400):
                try:
                    os.remove(fpath)
                    logger.info(f"Removed old log file: {f}")
                except Exception as e:
                    logger.warning(f"Failed to remove log {f}: {e}")

    # 2. Cleanup Temp
    temp_dir = "temp"
    if os.path.exists(temp_dir):
        for f in os.listdir(temp_dir):
            fpath = os.path.join(temp_dir, f)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
                else:
                    shutil.rmtree(fpath)
            except Exception as e:
                logger.warning(f"Failed to cleanup temp item {f}: {e}")

    # 3. Database Vacuum
    try:
        from core.database import FatwaDatabaseManager
        from core.bot_db import BotDatabaseManager
        
        fatwa_db = FatwaDatabaseManager()
        bot_db = BotDatabaseManager()
        
        await fatwa_db.vacuum()
        await bot_db.vacuum()
        logger.info("Maintenance databases vacuuming finished.")
    except Exception as e:
        logger.warning(f"Database maintenance failed: {e}")

    logger.info("✅ Maintenance job finished.")
