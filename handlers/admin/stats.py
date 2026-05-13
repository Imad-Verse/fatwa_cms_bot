import asyncio
import html
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone, time as dt_time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from telegram.error import BadRequest

from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import BACKUP_DIR
from core.utils import (
    sanitize_input, create_main_keyboard, 
    back_to_categories_keyboard, escape_markdown, notify_new_subscription,
    safe_reply_text, safe_edit_message_text
)
from handlers.general import cancel_operation, start_refresh, back_to_main
from handlers.admin.panel import admin_panel
from handlers.admin.settings import _WEEKDAYS_AR

logger = logging.getLogger(__name__)

# Singletons for Database Managers
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الإحصائيات"""
    query = update.callback_query
    if query: await query.answer()

    if not await bot_db.is_admin(update.effective_user.id):
        if query: await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    try:
        stats = await db.get_statistics()
        bot_stats = await bot_db.get_statistics()
        stats.update(bot_stats)

        # عدّاد "المضافة هذا الأسبوع" يبدأ من فجر الجمعة (التوقيت المحلي).
        local_now = datetime.now().astimezone()
        days_since_friday = (local_now.weekday() - 4) % 7  # Friday = 4
        friday_start_local = (local_now - timedelta(days=days_since_friday)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        friday_start_utc = friday_start_local.astimezone(timezone.utc).replace(tzinfo=None)
        weekly_added_count = await db.count_fatwas_since(friday_start_utc.strftime("%Y-%m-%d %H:%M:%S"))

        daily_time = await bot_db.get_setting("daily_publish_time", "12:00") or "12:00"
        weekly_time = await bot_db.get_setting("weekly_report_time", "08:00") or "08:00"
        weekly_day_raw = await bot_db.get_setting("weekly_report_weekday", "4") or "4"
        try:
            weekly_day_idx = int(weekly_day_raw)
        except (TypeError, ValueError):
            weekly_day_idx = 4
        weekly_day_name = _WEEKDAYS_AR[weekly_day_idx] if 0 <= weekly_day_idx < len(_WEEKDAYS_AR) else "غير محدد"

        text_out = (
            "📊 **إحصائيات البوت**\n\n"
            f"👁️ المشاهدات: {stats.get('total_views', 0)}\n"
            f"📚 إجمالي الفتاوى: {stats.get('total_fatwas', 0)}\n"
            f"📢 المنشورة: {stats.get('published_fatwas', 0)}\n"
            f"📝 المسودات: {stats.get('draft_fatwas', 0)}\n"
            f"🔥 المضافة هذا الأسبوع: {weekly_added_count}\n"
            f"🏷️ التصنيفات: {stats.get('categories', 0)}\n"
            f"👤 عداد المشايخ: {stats.get('scholars', 0)}\n\n"
            f"📢 القنوات: {stats.get('channels', 0)}\n"
            f"👥 المجموعات: {stats.get('groups', 0)}\n"
            f"👤 المشتركين: {stats.get('subscribers', 0)}\n\n"
            f"⏰ وقت النشر اليومي: {daily_time}\n"
            f"📅 موعد التقرير الأسبوعي: {weekly_day_name} - {weekly_time}\n\n"
        )

        # إضافة أكثر الفتاوى تفضيلاً
        top_favs = await db.get_top_favorites(5)
        if top_favs:
            text_out += "⭐ **أكثر الفتاوى تفضيلاً:**\n"
            for i, fav in enumerate(top_favs, 1):
                text_out += f"{i}. {escape_markdown(fav['title'])} ({fav['fav_count']})\n"

        keyboard = [
            [InlineKeyboardButton("🔄 تحديث", callback_data="stats"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
        ]
        
        if query:
            await safe_edit_message_text(query, text_out, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await safe_reply_text(update, text_out, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    except BadRequest as e:
        if "Message is not modified" in str(e):
            if query: await query.answer("⚠️ البيانات محدثة بالفعل", show_alert=False)
        else:
            logger.error(f"Error in show_statistics: {e}")
            raise e
    except Exception as e:
        logger.error(f"Error in show_statistics: {e}")
        error_msg = "❌ حدث خطأ أثناء تحميل الإحصائيات."
        if query: await safe_edit_message_text(query, error_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]))
        else: await safe_reply_text(update, error_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]))

def cleanup_old_backups(backup_dir: str, max_age_days: int = 7) -> int:
    """حذف النسخ الاحتياطية التي مر عليها أكثر من أسبوع."""
    try:
        now = time.time()
        max_age_seconds = max_age_days * 86400
        count = 0
        if not os.path.exists(backup_dir):
            return 0
            
        for f in os.listdir(backup_dir):
            path = os.path.join(backup_dir, f)
            if os.path.isfile(path):
                if os.stat(path).st_mtime < now - max_age_seconds:
                    os.remove(path)
                    count += 1
        return count
    except Exception as e:
        logger.error(f"Error during backup cleanup: {e}")
        return 0

async def backup_database_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنشاء نسخ احتياطية لقواعد البيانات (الفتاوى + المستخدمين) وإرسالها للمدير"""
    query = update.callback_query
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return
    await query.answer("جاري النسخ...")

    # اسم الملف مع التاريخ
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 1. قاعدة بيانات الفتاوى (DB + JSON)
    fatwa_db_filename = f"fatwa_backup_{timestamp}.db"
    fatwa_json_filename = f"fatwa_backup_{timestamp}.json"
    fatwa_db_path = os.path.join(BACKUP_DIR, fatwa_db_filename)
    fatwa_json_path = os.path.join(BACKUP_DIR, fatwa_json_filename)

    success_fatwa_db = await db.backup_database(fatwa_db_path)
    success_fatwa_json = await db.export_json(fatwa_json_path)

    # 2. قاعدة بيانات البوت (المستخدمين والقنوات والمجموعات)
    bot_db_filename = f"bot_internal_backup_{timestamp}.db"
    bot_db_path = os.path.join(BACKUP_DIR, bot_db_filename)
    success_bot_db = await bot_db.backup_database(bot_db_path)

    if not success_fatwa_db and not success_fatwa_json and not success_bot_db:
        await safe_edit_message_text(query, "❌ فشل النسخ الاحتياطي بالكامل.")
        return

    # إرسال الملفات للمدير
    if success_fatwa_db:
        with open(fatwa_db_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                caption=f"✅ نسخة قاعدة بيانات الفتاوى (DB): {timestamp}"
            )

    if success_fatwa_json:
        with open(fatwa_json_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                caption=f"✅ نسخة قاعدة بيانات الفتاوى (JSON): {timestamp}"
            )

    if success_bot_db:
        with open(bot_db_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                caption=f"✅ نسخة قاعدة بيانات البوت (المستخدمين/القنوات/المجموعات): {timestamp}"
            )

    # 3. تنظيف النسخ القديمة
    deleted_count = cleanup_old_backups(BACKUP_DIR)
    if deleted_count > 0:
        await query.message.reply_text(f"🧹 تم تنظيف {deleted_count} نسخة احتياطية قديمة (أقدم من أسبوع).")

# ==================== إدارة الروابط (Missing Links) ====================

async def manage_links_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة إدارة الروابط"""
    query = update.callback_query
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🔗 روابط مصدر ناقصة", callback_data="missing_links_source")],
        [InlineKeyboardButton("🔊 روابط صوتية ناقصة", callback_data="missing_links_audio")],
        [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
    ]

    await safe_edit_message_text(
        query,
        "🔗 **إدارة الروابط**\n\nاختر نوع الروابط التي تريد مراجعتها:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_missing_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الفتاوى الناقصة روابط"""
    query = update.callback_query
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return
    await query.answer()

    data = query.data
    # data format: missing_links_{type} or missing_links_{type}_{page}

    parts = data.split('_')
    link_type = parts[2] # source or audio
    page = int(parts[3]) if len(parts) > 3 else 0

    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE
    
    try:
        fatwas, total_count = await db.get_fatwas_missing_link(link_type, limit=ITEMS_PER_PAGE, offset=offset)
    except Exception:
        logger.exception("Failed to load missing links")
        await safe_edit_message_text(query, "❌ حدث خطأ أثناء تحميل البيانات.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_links")]]))
        return

    link_label = "مصدر" if link_type == 'source' else "صوتية"

    if not fatwas and page == 0:
        await safe_edit_message_text(
            query,
            f"✅ لا توجد فتاوى ينقصها روابط {link_label}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة الروابط", callback_data="manage_links")]])
        )
        return

    text = f"🔗 **فتاوى ينقصها روابط {link_label}** (صفحة {page + 1})\nإجمالي العدد: {total_count}\n\n"
    keyboard = []

    for fatwa in fatwas:
        # Title Button (to view/edit)
        safe_title = escape_markdown(fatwa['title'])
        keyboard.append([InlineKeyboardButton(f"🔸 {safe_title}", callback_data=f"view_{fatwa['id']}_missing_{link_type}_{page}")])

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"missing_links_{link_type}_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"missing_links_{link_type}_{page+1}"))

    if nav_buttons:
        # keyboard.insert(0, nav_buttons) # Top - REMOVED
        keyboard.append(nav_buttons)    # Bottom

    keyboard.append([InlineKeyboardButton("🔙 إدارة الروابط", callback_data="manage_links")])

    if query:
        await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await safe_reply_text(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# تعريف topic_conv في نهاية الملف لضمان تعريف جميع المعالجات المستخدمة


# ==============================================================================
# 🔗 القسم 8: إدارة المصادر والروابط (Sources & Links)
# ==============================================================================

