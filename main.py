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

# 2. Imports and Configuration
from core.config import TELEGRAM_TOKEN, PROXY_URL, REQUEST_TIMEOUT, LOGS_DIR, OWNER_ID, DEVELOPER_IDENTITY

from core.config import TELEGRAM_TOKEN, PROXY_URL, REQUEST_TIMEOUT, LOGS_DIR, OWNER_ID, DEVELOPER_IDENTITY
from core.utils import SingletonLock
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager

# استيراد المعالجات
from handlers.general import maintenance_mode_guard
from handlers.registry import register_all_handlers
from handlers.channels import daily_fatwa_job, weekly_fatwa_report_job

from core.logger import logger

# إسكات السجلات المزعجة من المكتبات الخارجية
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
async def verify_license(app):
    """التحقق من ترخيص تشغيل البوت وحماية حقوق المطور."""
    try:
        # 1. التحقق من تطابق المالك مع المطور أو وجود تصريح
        license_key = os.getenv("LICENSE_KEY", "NOT_SET")
        # كود التحقق السري (مثال: يمكن للمطور توليد كود خاص لكل مستخدم)
        expected_key = f"verified_{DEVELOPER_IDENTITY}"
        
        if OWNER_ID != DEVELOPER_IDENTITY and license_key != expected_key:
            logger.critical("❌ [LICENSE ERROR] This copy of Fatwa CMS is not authorized for this Owner ID.")
            print("\n" + "!"*60)
            print("   CRITICAL ERROR: UNAUTHORIZED DISTRIBUTION DETECTED")
            print("   This software is protected. Please contact the developer.")
            print("   Developer: @abulharith_imad")
            print("!"*60 + "\n")
            return False
        
        # 2. التحقق من وجود ملف الترخيص
        if not os.path.exists("LICENSE"):
            logger.warning("⚠️ LICENSE file is missing. Restoration required for full compliance.")
            
        return True
    except Exception as e:
        logger.error(f"License verification failed: {e}")
        return False

async def main():
    """الدالة الرئيسية لنقطة انطلاق البوت (Async)."""
    # 0. التحقق من الترخيص وحقوق المطور
    if not await verify_license(None):
        return

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

    log_level = os.getenv("TITAN_LOG_LEVEL", "INFO").upper()
    if log_level in ["INFO", "DEBUG"]:
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
    app.add_handler(TypeHandler(Update, maintenance_mode_guard), group=-1)

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

    # التشغيل
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logger.success("Bot is online and ready to serve!")
    if log_level in ["INFO", "DEBUG"]:
        print("\n" + "="*50)
        print("      [ BOT IS RUNNING ]")
        print("="*50 + "\n")

    # الانتظار بشكل آمن
    try:
        stop_event = asyncio.Event()
        await stop_event.wait()
    finally:
        logger.info("Shutting down bot...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
