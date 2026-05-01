"""
الملف الرئيسي (main.py)
-----------------------
نقطة انطلاق البوت.
يقوم بـ:
1. تهيئة التطبيق (Application).
2. تسجيل جميع المعالجات (Handlers).
3. بدء التشغيل (Polling).
"""

import os
import logging
import warnings
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.warnings import PTBUserWarning

# 1. تصفية التحذيرات قبل استيراد المعالجات
warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler, ChatMemberHandler,
    TypeHandler, ApplicationHandlerStop
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
from handlers.general import (
    start, help_info, cancel_operation, back_to_main, start_refresh, error_handler,
    how_to_add_bot, our_bots, noop, show_add_bot_tutorial
)
from handlers.search import (
    search_conv, handle_search_pagination, search_latest, search_popular, show_scholar_fatwas_by_id
)
# استيراد المعالجات
from handlers.general import (
    start, help_info, cancel_operation, back_to_main, start_refresh, error_handler,
    how_to_add_bot, our_bots, noop, show_add_bot_tutorial
)
from handlers.search import (
    search_conv, handle_search_pagination, search_latest, search_popular, show_scholar_fatwas_by_id
)
from handlers.fatwa import (
    add_fatwa_conv, edit_conv, view_fatwa, show_related_fatwas, publish_fatwa,
    copy_fatwa_full, delete_fatwa_confirm, delete_fatwa_final, delete_fatwa_from_all,
    broadcast_fatwa, handle_edit_cat_search, handle_receive_new_cat, show_random_fatwa,
    continue_reading_fatwa
)
from handlers.favorites import toggle_favorite_handler, my_favorites_handler, top_favorites_handler
from handlers.admin import (
    admin_panel, manage_admins, list_admins_handler, manage_categories,
    view_topics_handler, show_statistics, backup_database_handler,
    toggle_maintenance_mode,
    manage_links_panel, show_missing_links, show_duplicates,
    admin_conv, category_conv, topic_conv, show_admin_drafts,
    handle_category_type_filter, start_add_category_admin,
    manage_sources, manage_source, confirm_delete_source, delete_source_handler,
    confirm_delete_category_handler, delete_category_handler,
    source_conv, manage_scholars_panel, show_scholars_admin, view_scholar_admin,
    manage_subscribers, cleanup_inactive_subscribers, cancel_podcast_broadcast, scholar_conv, podcast_conv,
    settings_panel, start_set_weekly_day, apply_weekly_day,
    start_set_daily_time, start_set_weekly_time, handle_settings_time_input,
    test_notify
)
from handlers.channels import (
    track_chat_member, manage_channels_panel,
    toggle_auto_publish, show_channel_status, list_channels_handler, cleanup_inactive,
    daily_fatwa_job, weekly_fatwa_report_job,
    auto_publish_panel, force_publish_handler, start_schedule_fatwa_once, targeted_publish_panel,
    toggle_targeted_publish, start_select_publish_category, set_publish_category,
    start_search_publish_category, clear_publish_category_search, handle_publish_category_search_input,
    start_select_publish_topics, toggle_publish_topic, clear_publish_topics_selection
)

# إعداد السجلات (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "bot.log"), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

# إسكات السجلات المزعجة من المكتبات الخارجية
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

_DEVELOPER_CONTACT_URL = "https://t.me/abulharith_imad"
_guard_bot_db = BotDatabaseManager()


async def maintenance_mode_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إيقاف تفاعل المستخدمين غير المسؤولين أثناء وضع الصيانة."""
    if not update:
        return

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type != "private":
        return

    if _guard_bot_db.is_admin(user.id):
        return
    if _guard_bot_db.get_setting("maintenance_mode", "0") != "1":
        return

    maintenance_text = (
        "🚧 البوت في وضع الصيانة حاليًا.\n"
        "يرجى المحاولة لاحقًا."
    )
    maintenance_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 راسل المطور", url=_DEVELOPER_CONTACT_URL)]
    ])

    if update.callback_query:
        try:
            await update.callback_query.answer("🚧 البوت في وضع الصيانة", show_alert=False)
        except Exception:
            pass
        if update.callback_query.message:
            await update.callback_query.message.reply_text(maintenance_text, reply_markup=maintenance_markup)
    elif update.effective_message:
        await update.effective_message.reply_text(maintenance_text, reply_markup=maintenance_markup)

    raise ApplicationHandlerStop

def main():
    """الدالة الرئيسية لنقطة انطلاق البوت."""
    logger.info("Starting Fatwa Bot...")

    # تهيئة اتصالات قاعدة البيانات
    fatwa_db = FatwaDatabaseManager()
    bot_db = BotDatabaseManager()

    # تنفيذ عملية ملء حقل created_at للفتاوى القديمة (تنفذ مرة واحدة فقط)
    try:
        if bot_db.get_setting("fatwa_created_at_backfill_done", "0") != "1":
            updated = fatwa_db.backfill_created_at()
            bot_db.set_setting("fatwa_created_at_backfill_done", "1")
            logger.info(f"Backfilled created_at for {updated} fatwas.")
    except Exception as e:
        logger.error(f"Backfill created_at failed: {e}")

    # -------------------------------------------------------------
    # 🎨 Beautiful English Startup Messages
    # -------------------------------------------------------------
    print("\n" + "="*50)
    print("   [ FATWA BOT SYSTEM ]")
    print("="*50 + "\n")
    logger.info("System is initializing...")

    # 1. التحقق من عدم تكرار التشغيل (Socket Lock)
    # يجب الاحتفاظ بالـ lock في متغير حتى لا يتم إغلاقه
    lock = SingletonLock()
    if not lock.acquire():
        logger.error("Bot is already running! (Socket Port Busy)")
        print("Bot is already running! (Socket Port Busy)")
        print("💡 Solution: Close any other 'python' windows or run 'taskkill /F /IM python.exe' in terminal.")
        return # الخروج من main

    # بناء التطبيق
    builder = ApplicationBuilder().token(TELEGRAM_TOKEN)

    # إضافة البروكسي إذا وجد
    if PROXY_URL:
        logger.info(f"🌐 Using proxy: {PROXY_URL}")
        builder.proxy_url(PROXY_URL)
        builder.get_updates_proxy_url(PROXY_URL)

    # ضبط المهل الزمنية (زيادة المهلة لتجنب ConnectTimeout)
    builder.connect_timeout(REQUEST_TIMEOUT)
    builder.read_timeout(REQUEST_TIMEOUT)
    builder.write_timeout(REQUEST_TIMEOUT)
    builder.pool_timeout(REQUEST_TIMEOUT)

    app = builder.build()

    # -------------------------
    # تسجيل المعالجات
    # -------------------------

    # 0. حارس وضع الصيانة (قبل كل المعالجات الأخرى)
    app.add_handler(TypeHandler(Update, maintenance_mode_guard), group=-1)

    # 1. الأوامر الأساسية
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_info))
    app.add_handler(CommandHandler("our_bots", our_bots))
    app.add_handler(CommandHandler("test_notify", test_notify))

    # 1.5. القائمة السفلية (Persistent Menu)
    # نضعها قبل المحادثات إذا أردنا أن تعمل دائماً، أو بعد المحادثات إذا أردنا أن تتوقف أثناء الإدخال.
    # User likely wants them to work always or cancel current op.
    # To act as "Cancel & GO", they should be registered BEFORE ConversationHandlers or AS Falbacks.
    # But usually text handlers are checked globally.
    # Let's add them here. If user is in a conversation, standard behavior depends on the conversation configuration.
    # Usually we add them as Fallbacks inside conversation if we want them to interrupt.
    # For now, simplistic approach: Global handler.
    app.add_handler(MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), start))
    app.add_handler(MessageHandler(filters.Regex("^🤖 بوتاتنا$"), our_bots))

    # إعدادات الجدولة (معالجة مباشرة حتى لو كان المستخدم داخل محادثة أخرى)
    app.add_handler(CallbackQueryHandler(start_set_daily_time, pattern='^set_daily_time$'))
    app.add_handler(CallbackQueryHandler(start_set_weekly_time, pattern='^set_weekly_time$'))
    app.add_handler(MessageHandler(filters.Regex(r'^\d{1,2}:\d{2}$') & (~filters.COMMAND) & filters.ChatType.PRIVATE, handle_settings_time_input, block=False))

    # 2. المحادثات (Conversations) - يجب أن تكون أولاً
    app.add_handler(add_fatwa_conv)
    app.add_handler(edit_conv)
    app.add_handler(search_conv)
    app.add_handler(admin_conv)
    app.add_handler(scholar_conv)
    app.add_handler(podcast_conv)
    app.add_handler(category_conv)
    app.add_handler(topic_conv)

    # Detect bot status in channels
    app.add_handler(ChatMemberHandler(track_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # 3. معالجات القائمة والتنقل
    app.add_handler(CallbackQueryHandler(start_refresh, pattern='^start_refresh$'))
    app.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_main$'))
    app.add_handler(CallbackQueryHandler(cancel_operation, pattern='^cancel$'))
    app.add_handler(CallbackQueryHandler(help_info, pattern='^help_info$'))
    app.add_handler(CallbackQueryHandler(how_to_add_bot, pattern='^how_to_add_bot$'))
    app.add_handler(CallbackQueryHandler(show_add_bot_tutorial, pattern='^show_add_bot_tutorial$'))
    app.add_handler(CallbackQueryHandler(noop, pattern='^noop$'))

    # 4. معالجات البحث (Paginations + Latest/Popular)
    app.add_handler(CallbackQueryHandler(handle_search_pagination, pattern='^res_page_'))
    app.add_handler(CallbackQueryHandler(search_latest, pattern='^search_latest$'))
    app.add_handler(CallbackQueryHandler(search_popular, pattern='^search_popular$'))

    # 5. معالجات الفتاوى (عرض، نشر، حذف)
    app.add_handler(CallbackQueryHandler(publish_fatwa, pattern=r'^publish_\d+'))
    app.add_handler(CallbackQueryHandler(delete_fatwa_confirm, pattern=r'^confirm_delete_\d+'))
    app.add_handler(CallbackQueryHandler(delete_fatwa_final, pattern=r'^delete_final_\d+'))
    app.add_handler(CallbackQueryHandler(delete_fatwa_from_all, pattern=r'^del_all_fatwa_\d+$'))
    app.add_handler(CallbackQueryHandler(copy_fatwa_full, pattern=r'^copy_full_\d+'))
    app.add_handler(CallbackQueryHandler(broadcast_fatwa, pattern=r'^broadcast_\d+'))
    app.add_handler(CallbackQueryHandler(show_random_fatwa, pattern=r'^random_fatwa(?:_\d+)?$'))
    app.add_handler(CallbackQueryHandler(continue_reading_fatwa, pattern=r'^continue_read_\d+(?:_.+)?$'))
    app.add_handler(CallbackQueryHandler(view_fatwa, pattern=r'^view_\d+'))
    app.add_handler(CallbackQueryHandler(show_related_fatwas, pattern=r'^related_fatwas_\d+'))
    app.add_handler(CallbackQueryHandler(show_scholar_fatwas_by_id, pattern=r'^scholar_fatwas_'))

    # 6. معالجات الإدارة
    app.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    app.add_handler(CallbackQueryHandler(settings_panel, pattern='^admin_settings$'))
    app.add_handler(CallbackQueryHandler(start_set_weekly_day, pattern='^set_weekly_day$'))
    app.add_handler(CallbackQueryHandler(apply_weekly_day, pattern='^weekly_day_'))
    app.add_handler(CallbackQueryHandler(show_admin_drafts, pattern='^admin_drafts'))
    app.add_handler(CallbackQueryHandler(show_duplicates, pattern='^admin_duplicates'))
    app.add_handler(CallbackQueryHandler(manage_admins, pattern='^manage_admins$'))
    app.add_handler(CallbackQueryHandler(manage_scholars_panel, pattern='^manage_scholars$'))
    app.add_handler(CallbackQueryHandler(show_scholars_admin, pattern='^scholars_list'))
    app.add_handler(CallbackQueryHandler(view_scholar_admin, pattern='^scholar_view_'))
    app.add_handler(CallbackQueryHandler(manage_subscribers, pattern='^manage_subscribers'))
    app.add_handler(CallbackQueryHandler(cleanup_inactive_subscribers, pattern='^cleanup_subscribers$'))
    app.add_handler(CallbackQueryHandler(cancel_podcast_broadcast, pattern='^podcast_cancel_'))
    app.add_handler(CallbackQueryHandler(list_admins_handler, pattern='^list_admins$'))
    app.add_handler(CallbackQueryHandler(manage_categories, pattern='^manage_categories'))
    app.add_handler(CallbackQueryHandler(manage_sources, pattern='^manage_sources'))
    app.add_handler(CallbackQueryHandler(manage_source, pattern='^manage_source_'))
    app.add_handler(CallbackQueryHandler(confirm_delete_source, pattern='^confirm_delete_source_'))
    app.add_handler(CallbackQueryHandler(delete_source_handler, pattern='^delete_source_'))
    app.add_handler(CallbackQueryHandler(confirm_delete_category_handler, pattern='^confirm_delete_category_'))
    app.add_handler(CallbackQueryHandler(delete_category_handler, pattern='^delete_category_'))
    app.add_handler(CallbackQueryHandler(handle_category_type_filter, pattern='^admin_cat_type_'))
    app.add_handler(CallbackQueryHandler(start_add_category_admin, pattern='^add_cat_(fiqh|topic)$'))
    app.add_handler(CallbackQueryHandler(view_topics_handler, pattern='^view_topics'))
    app.add_handler(CallbackQueryHandler(show_statistics, pattern='^stats$'))
    app.add_handler(CallbackQueryHandler(backup_database_handler, pattern='^backup_db$'))
    app.add_handler(CallbackQueryHandler(toggle_maintenance_mode, pattern='^toggle_maintenance_mode$'))
    app.add_handler(CallbackQueryHandler(manage_links_panel, pattern='^manage_links$'))
    app.add_handler(CallbackQueryHandler(show_missing_links, pattern='^missing_links_'))
    app.add_handler(source_conv)

    # إدار القنوات (Callbacks)
    app.add_handler(CallbackQueryHandler(manage_channels_panel, pattern='^manage_channels$'))
    app.add_handler(CallbackQueryHandler(toggle_auto_publish, pattern='^toggle_auto_publish$')) # Deprecated usage but function updated to master
    app.add_handler(CallbackQueryHandler(show_channel_status, pattern='^status_(channels|groups)'))
    app.add_handler(CallbackQueryHandler(list_channels_handler, pattern='^list_'))
    app.add_handler(CallbackQueryHandler(cleanup_inactive, pattern='^cleanup_(channel|group)$'))

    # إدارة النشر التلقائي (New)
    app.add_handler(CallbackQueryHandler(auto_publish_panel, pattern='^auto_publish_panel$'))
    app.add_handler(CallbackQueryHandler(force_publish_handler, pattern='^force_publish_now$'))
    app.add_handler(CallbackQueryHandler(start_schedule_fatwa_once, pattern='^schedule_fatwa_once$'))
    app.add_handler(CallbackQueryHandler(toggle_auto_publish, pattern='^toggle_auto_publish_master$')) # Reusing toggle_auto_publish
    app.add_handler(CallbackQueryHandler(targeted_publish_panel, pattern='^targeted_publish_panel$'))
    app.add_handler(CallbackQueryHandler(toggle_targeted_publish, pattern='^toggle_targeted_publish$'))
    app.add_handler(CallbackQueryHandler(start_select_publish_category, pattern='^sel_pub_cat_start$'))
    app.add_handler(CallbackQueryHandler(start_select_publish_category, pattern='^sel_pub_cat_page_'))
    app.add_handler(CallbackQueryHandler(start_search_publish_category, pattern='^search_pub_cat$'))
    app.add_handler(CallbackQueryHandler(clear_publish_category_search, pattern='^clear_pub_cat_search$'))
    app.add_handler(CallbackQueryHandler(set_publish_category, pattern='^set_pub_cat_'))
    app.add_handler(CallbackQueryHandler(start_select_publish_topics, pattern='^sel_pub_top_start$'))
    app.add_handler(CallbackQueryHandler(start_select_publish_topics, pattern='^sel_pub_top_page_'))
    app.add_handler(CallbackQueryHandler(toggle_publish_topic, pattern='^toggle_pub_top_'))
    app.add_handler(CallbackQueryHandler(clear_publish_topics_selection, pattern='^clear_pub_topics$'))

    # Message Handler for Search Input (Register globally for now, handles state internally)
    # Fix: Ensure it only runs in PRIVATE chats to avoid catching channel posts where bot is admin
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & filters.ChatType.PRIVATE, handle_publish_category_search_input))

    # 7. معالجات المفضلة (تم إضافتها الآن)
    app.add_handler(CallbackQueryHandler(toggle_favorite_handler, pattern='^toggle_fav_'))
    app.add_handler(CallbackQueryHandler(my_favorites_handler, pattern='^my_favorites$'))
    app.add_handler(CallbackQueryHandler(my_favorites_handler, pattern='^fav_page_'))
    app.add_handler(CallbackQueryHandler(my_favorites_handler, pattern='^fav_sort_'))
    app.add_handler(CallbackQueryHandler(top_favorites_handler, pattern='^top_favorites$'))

    # 8. الجدولة (JobQueue) - 12:00 PM Daily
    if app.job_queue:
        import datetime
        # Daily auto publish time (Local Time)
        local_tz = datetime.datetime.now().astimezone().tzinfo
        daily_time_str = bot_db.get_setting("daily_publish_time", "12:00") or "12:00"
        try:
            dh, dm = daily_time_str.split(":")
            dhour = int(dh)
            dmin = int(dm)
        except Exception as e:
            logger.warning(f"Invalid daily_publish_time '{daily_time_str}', fallback to 12:00. Error: {e}")
            dhour, dmin = 12, 0
        t = datetime.time(dhour, dmin, 0, tzinfo=local_tz)

        app.job_queue.run_daily(daily_fatwa_job, t, name='daily_publish')
        logger.info(f"✅ Daily Auto-Publish Job scheduled for {dhour:02d}:{dmin:02d}")

        # Weekly report to users (runs at configured time, sends only on configured weekday)
        weekly_time_str = bot_db.get_setting("weekly_report_time", "12:00") or "12:00"
        weekly_weekday_str = bot_db.get_setting("weekly_report_weekday", "0") or "0"
        try:
            hh, mm = weekly_time_str.split(":")
            whour = int(hh)
            wmin = int(mm)
        except Exception as e:
            logger.warning(f"Invalid weekly_report_time '{weekly_time_str}', fallback to 12:00. Error: {e}")
            whour, wmin = 12, 0
        try:
            weekly_weekday = int(weekly_weekday_str)
        except Exception as e:
            logger.warning(f"Invalid weekly_report_weekday '{weekly_weekday_str}', fallback to 0. Error: {e}")
            weekly_weekday = 0
        weekly_t = datetime.time(whour, wmin, 0, tzinfo=local_tz)
        app.bot_data["weekly_report_weekday"] = weekly_weekday
        app.job_queue.run_daily(weekly_fatwa_report_job, weekly_t, name='weekly_report')

    # معالجة الأخطاء
    app.add_error_handler(error_handler)

    logger.info("Bot is online and ready to serve!")
    print("\n" + "="*50)
    print("      [ BOT IS RUNNING ]")
    print("="*50 + "\n")
    # Ensure an event loop exists (Python 3.12+ may not create one by default)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling()

if __name__ == '__main__':
    main()
