import os
import logging
import warnings
import asyncio
import datetime
from telegram import Update
from telegram.warnings import PTBUserWarning

# 1. تصفية التحذيرات قبل استيراد المعالجات
warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram.ext import (
    ApplicationBuilder, ContextTypes, TypeHandler
)

# Workarounds for PTB 20.8 __slots__ bugs (Updater + Application weakref)
import telegram.ext._updater as _ptb_updater
import telegram.ext._application as _ptb_application
import telegram.ext._applicationbuilder as _ptb_builder

if "_Updater__polling_cleanup_cb" not in getattr(_ptb_updater.Updater, "__slots__", ()):
    class _PatchedUpdater(_ptb_updater.Updater):
        __slots__ = ("_Updater__polling_cleanup_cb",)
    _ptb_updater.Updater = _PatchedUpdater
    _ptb_builder.Updater = _PatchedUpdater

if "__weakref__" not in getattr(_ptb_application.Application, "__slots__", ()):
    class _PatchedApplication(_ptb_application.Application):
        __slots__ = ("__weakref__",)
    _ptb_application.Application = _PatchedApplication
    _ptb_builder.Application = _PatchedApplication

from core.config import TELEGRAM_TOKEN, PROXY_URL, REQUEST_TIMEOUT, LOGS_DIR
from core.utils import SingletonLock
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager

# استيراد المعالجات
from handlers.general import maintenance_mode_guard
from handlers.registry import register_all_handlers
from handlers.channels import daily_fatwa_job, weekly_fatwa_report_job

import logging.handlers

# إعداد السجلات (Logging)
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = os.path.join(LOGS_DIR, "bot.log")

# تدوير السجلات (Log Rotation): 10MB لكل ملف، مع الاحتفاظ بـ 5 نسخ قديمة
file_handler = logging.handlers.RotatingFileHandler(
    log_file, 
    maxBytes=10*1024*1024, 
    backupCount=5, 
    encoding='utf-8'
)
file_handler.setFormatter(log_formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)

# إسكات السجلات المزعجة من المكتبات الخارجية
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

async def maintenance_mode_guard_internal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إيقاف تفاعل المستخدمين غير المسؤولين أثناء وضع الصيانة."""
    return await maintenance_mode_guard(update, context)

async def main():
    """الدالة الرئيسية لنقطة انطلاق البوت (Async)."""
    logger.info("Starting Fatwa Bot...")

    # تهيئة اتصالات قاعدة البيانات (Async)
    fatwa_db = FatwaDatabaseManager()
    bot_db = BotDatabaseManager()
    
    await fatwa_db.init_db()
    await bot_db.init_db()

    # تنفيذ عملية ملء حقل created_at للفتاوى القديمة (تنفذ مرة واحدة فقط)
    try:
        if await bot_db.get_setting("fatwa_created_at_backfill_done", "0") != "1":
            updated = await fatwa_db.backfill_created_at()
            await bot_db.set_setting("fatwa_created_at_backfill_done", "1")
            logger.info(f"Backfilled created_at for {updated} fatwas.")
    except Exception as e:
        logger.error(f"Backfill created_at failed: {e}")

    # 🎨 Beautiful English Startup Messages
    print("\n" + "="*50)
    print("   [ FATWA BOT SYSTEM ]")
    print("="*50 + "\n")
    logger.info("System is initializing...")

    # 1. التحقق من عدم تكرار التشغيل (Socket Lock)
    lock = SingletonLock()
    if not lock.acquire():
        logger.error("Bot is already running! (Socket Port Busy)")
        print("Bot is already running! (Socket Port Busy)")
        print("💡 Solution: Close any other 'python' windows or run 'taskkill /F /IM python.exe' in terminal.")
        return 

    # بناء التطبيق
    builder = ApplicationBuilder().token(TELEGRAM_TOKEN)

    # إضافة البروكسي إذا وجد
    if PROXY_URL and PROXY_URL.strip():
        logger.info(f"🌐 Using proxy: {PROXY_URL}")
        builder.proxy(PROXY_URL.strip())
        builder.get_updates_proxy(PROXY_URL.strip())

    # ضبط المهل الزمنية
    builder.connect_timeout(REQUEST_TIMEOUT)
    builder.read_timeout(REQUEST_TIMEOUT)
    builder.write_timeout(REQUEST_TIMEOUT)
    builder.pool_timeout(REQUEST_TIMEOUT)

    app = builder.build()

    # تسجيل المعالجات
    # 0. حارس وضع الصيانة (قبل كل المعالجات الأخرى)
    app.add_handler(TypeHandler(Update, maintenance_mode_guard_internal), group=-1)

    # تسجيل جميع المعالجات من السجل الموحد
    register_all_handlers(app)

    # 8. الجدولة (JobQueue)
    if app.job_queue:
        # Daily auto publish time (Local Time)
        local_tz = datetime.datetime.now().astimezone().tzinfo
        daily_time_str = await bot_db.get_setting("daily_publish_time", "12:00") or "12:00"
        try:
            dh, dm = daily_time_str.split(":")
            dhour, dmin = int(dh), int(dm)
        except Exception as e:
            logger.warning(f"Invalid daily_publish_time '{daily_time_str}', fallback to 12:00. Error: {e}")
            dhour, dmin = 12, 0
        t = datetime.time(dhour, dmin, 0, tzinfo=local_tz)

        app.job_queue.run_daily(daily_fatwa_job, t, name='daily_publish')
        logger.info(f"✅ Daily Auto-Publish Job scheduled for {dhour:02d}:{dmin:02d}")

        # Weekly report to users
        weekly_time_str = await bot_db.get_setting("weekly_report_time", "12:00") or "12:00"
        weekly_weekday_str = await bot_db.get_setting("weekly_report_weekday", "4") or "4"
        try:
            hh, mm = weekly_time_str.split(":")
            whour, wmin = int(hh), int(mm)
        except Exception as e:
            logger.warning(f"Invalid weekly_report_time '{weekly_time_str}', fallback to 12:00. Error: {e}")
            whour, wmin = 12, 0
        try:
            weekly_weekday = int(weekly_weekday_str)
        except Exception as e:
            logger.warning(f"Invalid weekly_report_weekday '{weekly_weekday_str}', fallback to 4. Error: {e}")
            weekly_weekday = 4
            
        weekly_t = datetime.time(whour, wmin, 0, tzinfo=local_tz)
        app.bot_data["weekly_report_weekday"] = weekly_weekday
        app.job_queue.run_daily(weekly_fatwa_report_job, weekly_t, name='weekly_report')

        # Periodic Maintenance (Every 24 hours)
        from core.maintenance import periodic_maintenance_job
        app.job_queue.run_repeating(periodic_maintenance_job, interval=86400, first=60, name='maintenance')

    logger.info("Bot is online and ready to serve!")
    print("\n" + "="*50)
    print("      [ BOT IS RUNNING ]")
    print("="*50 + "\n")
    
    # استخدام run_polling كـ async إذا أردنا أو استخدام الاختصار المعتاد
    # PTB's run_polling() is blocking but can be used in a wrapper. 
    # For a fully async start, we can use app.initialize(), app.start(), app.updater.start_polling()
    # But PTB's Application.run_polling() handles the event loop if not already running.
    # Since we are already in an async loop (asyncio.run), we should use a non-blocking start or just call it if it supports it.
    # Actually, Application.run_polling() is designed to be the entry point.
    # However, if we are ALREADY in an async loop, we should use:
    # await app.initialize()
    # await app.start()
    # await app.updater.start_polling()
    # await idle()
    
    # BUT, Application.run_polling() is very convenient. Let's see if we can use it.
    # In PTB 20.x, run_polling is synchronous and starts its own loop.
    # To run it from an existing loop, we might need a different approach.
    
    # Wait, I'll use the standard way:
    # app.run_polling() inside a synchronous main or use the async approach.
    
    # Actually, I'll change main() back to synchronous and just run the async parts using asyncio.run.
    # But it's better to have one loop.
    
    # Let's do it this way:
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep the script running
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
