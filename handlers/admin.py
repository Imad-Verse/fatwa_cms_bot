"""
Administrative Handlers (handlers/admin.py)
------------------------------------------
Contains all administrative functions for the Fatwa Bot:
- Admin Panel & Statistics.
- Admin Management (Add/Remove).
- Categorization (Categories & Topics).
- Scholar Management.
- Publishing Settings (Auto-publish, Job scheduling).
- Broadcast (Podcast).
- Database Backup.
"""

import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone, time as dt_time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from telegram.error import BadRequest

from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import *
from core.utils import (
    sanitize_input, create_main_keyboard, 
    back_to_categories_keyboard, escape_markdown, notify_new_subscription
)
from handlers.general import cancel_operation, start_refresh, back_to_main

logger = logging.getLogger(__name__)

# Singletons for Database Managers
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# ==============================================================================
# 🛠️ القسم 1: لوحة التحكم والوصول الأساسي
# ==============================================================================

async def test_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test notification sending to owner"""
    user_id = update.effective_user.id
    if not bot_db.is_admin(user_id):
        return

    await update.message.reply_text("⏳ جاري إرسال إشعار تجريبي...")
    try:
        await notify_new_subscription(
            context.bot,
            'user',
            {'id': 12345, 'name': 'مستخدم تجريبي', 'username': 'test_user'},
            context
        )
        await update.message.reply_text("✅ تم إرسال الإشعار! تحقق من رسائلك.")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال: {e}")

# ==================== لوحة الإدارة ====================

def _build_admin_panel_payload() -> tuple[str, InlineKeyboardMarkup]:
    """بناء نص وأزرار لوحة الإدارة مع حالة وضع الصيانة."""
    maintenance_enabled = (bot_db.get_setting("maintenance_mode", "0") == "1")
    maintenance_btn = "🟢 وضع الصيانة" if maintenance_enabled else "🔴 وضع الصيانة"
    maintenance_status = "🟢 مفعّل" if maintenance_enabled else "🔴 غير مفعّل"

    keyboard = [
        [InlineKeyboardButton("➕ إضافة فتوى", callback_data="add_fatwa")],
        [InlineKeyboardButton("📝 المسودات", callback_data="admin_drafts"), InlineKeyboardButton("🔄 المكررة", callback_data="admin_duplicates")],
        [InlineKeyboardButton("🏷️ إدارة التصنيفات", callback_data="manage_categories"), InlineKeyboardButton("👤 إدارة العلماء", callback_data="manage_scholars")],
        [InlineKeyboardButton("🔗 إدارة الروابط", callback_data="manage_links"), InlineKeyboardButton("📚 إدارة المصادر", callback_data="manage_sources")],
        [InlineKeyboardButton("⚙️ إدارة النشر التلقائي", callback_data="auto_publish_panel"), InlineKeyboardButton("⏱️ إعدادات الجدولة", callback_data="admin_settings")],
        [InlineKeyboardButton("👥 إدارة المشتركين", callback_data="manage_subscribers"), InlineKeyboardButton("📢 إدارة القنوات", callback_data="manage_channels")],
        [InlineKeyboardButton("🎙️ بودكاست", callback_data="podcast_panel"), InlineKeyboardButton("🧑‍💼 إدارة المسؤولين", callback_data="manage_admins")],
        [InlineKeyboardButton(maintenance_btn, callback_data="toggle_maintenance_mode"), InlineKeyboardButton("💾 نسخة احتياطية", callback_data="backup_db")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
    ]

    text = (
        f"⚙️ **لوحة الإدارة**\n"
        f"حالة وضع الصيانة: {maintenance_status}\n\n"
        "اختر من العمليات التالية:"
    )
    return text, InlineKeyboardMarkup(keyboard)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة الإدارة"""
    query = update.callback_query
    await query.answer()

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    text, markup = _build_admin_panel_payload()
    await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')


async def toggle_maintenance_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل وضع الصيانة وتشغيله/إيقافه."""
    query = update.callback_query

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    current = (bot_db.get_setting("maintenance_mode", "0") == "1")
    new_value = "0" if current else "1"
    bot_db.set_setting("maintenance_mode", new_value)

    if new_value == "1":
        await query.answer("🟢 تم تفعيل وضع الصيانة")
    else:
        await query.answer("🔴 تم إيقاف وضع الصيانة")

    text, markup = _build_admin_panel_payload()
    await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')

# ==================== إعدادات الجدولة ====================

_WEEKDAYS_AR = [
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
]

def _parse_time_input(text: str):
    if not text:
        return None
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return hh, mm

def _format_time(hh: int, mm: int) -> str:
    return f"{hh:02d}:{mm:02d}"

def _reschedule_job_daily(context: ContextTypes.DEFAULT_TYPE, hour: int, minute: int) -> bool:
    jq = context.application.job_queue if context and context.application else None
    if not jq:
        return False
    for job in jq.get_jobs_by_name("daily_publish"):
        job.schedule_removal()
    local_tz = datetime.now().astimezone().tzinfo
    t = dt_time(hour, minute, 0, tzinfo=local_tz)
    from handlers.channels import daily_fatwa_job
    jq.run_daily(daily_fatwa_job, t, name="daily_publish")
    return True

def _reschedule_job_weekly(context: ContextTypes.DEFAULT_TYPE, hour: int, minute: int) -> bool:
    jq = context.application.job_queue if context and context.application else None
    if not jq:
        return False
    for job in jq.get_jobs_by_name("weekly_report"):
        job.schedule_removal()
    local_tz = datetime.now().astimezone().tzinfo
    t = dt_time(hour, minute, 0, tzinfo=local_tz)
    from handlers.channels import weekly_fatwa_report_job
    jq.run_daily(weekly_fatwa_report_job, t, name="weekly_report")
    return True

async def settings_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة إعدادات الجدولة"""
    query = update.callback_query
    await query.answer()

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    context.user_data.pop("settings_input_mode", None)
    daily_time = bot_db.get_setting("daily_publish_time", "12:00") or "12:00"
    weekly_time = bot_db.get_setting("weekly_report_time", "08:00") or "08:00"
    weekly_day_raw = bot_db.get_setting("weekly_report_weekday", "4") or "4"
    try:
        weekly_day = int(weekly_day_raw)
    except (TypeError, ValueError):
        weekly_day = 4
    weekly_day_name = _WEEKDAYS_AR[weekly_day] if 0 <= weekly_day < len(_WEEKDAYS_AR) else "غير محدد"

    text = (
        "⏱️ **إعدادات الجدولة**\n\n"
        f"⏰ وقت النشر اليومي: `{daily_time}`\n"
        f"📅 التقرير الأسبوعي: {weekly_day_name} - `{weekly_time}`\n\n"
        "اختر ما تريد تعديله:"
    )

    keyboard = [
        [InlineKeyboardButton("⏰ تغيير وقت النشر اليومي", callback_data="set_daily_time")],
        [InlineKeyboardButton("📅 تغيير يوم التقرير الأسبوعي", callback_data="set_weekly_day")],
        [InlineKeyboardButton("⏰ تغيير وقت التقرير الأسبوعي", callback_data="set_weekly_time")],
        [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def start_set_daily_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END
    context.user_data["settings_input_mode"] = "daily_time"
    await query.edit_message_text(
        "⏰ **تغيير وقت النشر اليومي**\n\nأرسل الوقت بصيغة `HH:MM` (مثال: 08:00)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]]),
        parse_mode='Markdown'
    )
    return STATE_SETTINGS_DAILY_TIME

async def start_set_weekly_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END
    context.user_data["settings_input_mode"] = "weekly_time"
    await query.edit_message_text(
        "⏰ **تغيير وقت التقرير الأسبوعي**\n\nأرسل الوقت بصيغة `HH:MM` (مثال: 08:00)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]]),
        parse_mode='Markdown'
    )
    return STATE_SETTINGS_WEEKLY_TIME

async def start_set_weekly_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    current_raw = bot_db.get_setting("weekly_report_weekday", "4") or "4"
    try:
        current_day = int(current_raw)
    except (TypeError, ValueError):
        current_day = 4

    keyboard = []
    row = []
    for idx, name in enumerate(_WEEKDAYS_AR):
        label = f"{name} ✅" if idx == current_day else name
        row.append(InlineKeyboardButton(label, callback_data=f"weekly_day_{idx}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")])

    await query.edit_message_text(
        "📅 **اختر يوم التقرير الأسبوعي**:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def apply_weekly_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return
    try:
        day_idx = int(query.data.split("_")[-1])
    except (TypeError, ValueError, IndexError):
        await query.answer("❌ قيمة غير صحيحة", show_alert=True)
        return

    if not (0 <= day_idx <= 6):
        await query.answer("❌ يوم غير صحيح", show_alert=True)
        return

    bot_db.set_setting("weekly_report_weekday", str(day_idx))
    if context and context.application:
        context.application.bot_data["weekly_report_weekday"] = day_idx

    await settings_panel(update, context)

async def receive_daily_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["settings_input_mode"] = "daily_time"
    text = update.message.text.strip() if update.message else ""
    parsed = _parse_time_input(text)
    if not parsed:
        await update.message.reply_text(
            "❌ صيغة الوقت غير صحيحة. أرسل الوقت بصيغة `HH:MM` (مثال: 08:00)",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]])
        )
        return STATE_SETTINGS_DAILY_TIME

    hh, mm = parsed
    time_str = _format_time(hh, mm)
    bot_db.set_setting("daily_publish_time", time_str)
    _reschedule_job_daily(context, hh, mm)
    context.user_data.pop("settings_input_mode", None)

    await update.message.reply_text(
        f"✅ تم تحديث وقت النشر اليومي إلى: `{time_str}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ إعدادات الجدولة", callback_data="admin_settings")]])
    )
    return ConversationHandler.END

async def receive_weekly_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["settings_input_mode"] = "weekly_time"
    text = update.message.text.strip() if update.message else ""
    parsed = _parse_time_input(text)
    if not parsed:
        await update.message.reply_text(
            "❌ صيغة الوقت غير صحيحة. أرسل الوقت بصيغة `HH:MM` (مثال: 08:00)",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]])
        )
        return STATE_SETTINGS_WEEKLY_TIME

    hh, mm = parsed
    time_str = _format_time(hh, mm)
    bot_db.set_setting("weekly_report_time", time_str)
    _reschedule_job_weekly(context, hh, mm)
    context.user_data.pop("settings_input_mode", None)

    await update.message.reply_text(
        f"✅ تم تحديث وقت التقرير الأسبوعي إلى: `{time_str}`",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ إعدادات الجدولة", callback_data="admin_settings")]])
    )
    return ConversationHandler.END

async def handle_settings_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settings time input even if no conversation is active."""
    mode = context.user_data.get("settings_input_mode")
    if mode == "daily_time":
        return await receive_daily_time(update, context)
    if mode == "weekly_time":
        return await receive_weekly_time(update, context)
    return

async def show_admin_drafts(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int | None = None):
    """عرض المسودات مع أزرار التحكم"""
    query = update.callback_query
    await query.answer()

    if page is None:
        page = 0
        data = query.data
        if "admin_drafts_" in data:
            page = int(data.split("_")[-1])

    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    drafts, total_drafts = db.get_all_fatwas(status='draft', limit=ITEMS_PER_PAGE, offset=offset)

    # stats = db.get_statistics()
    # total_drafts = stats.get('draft_fatwas', 0)

    if not drafts and page == 0:
        await query.edit_message_text(
            "✅ لا توجد مسودات حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
        return

    text = f"📝 **المسودات** (صفحة {page + 1})\nإجمالي المسودات: {total_drafts}\n\n"
    keyboard = []

    for fatwa in drafts:
        # Title Button (acts as Label or View)
        keyboard.append([InlineKeyboardButton(f"🔸 {fatwa['title']}", callback_data=f"view_{fatwa['id']}_drafts_{page}")])

        # Action Buttons: Publish, View (Edit is inside View)
        btns = [
            InlineKeyboardButton("📢 نشر", callback_data=f"publish_{fatwa['id']}_drafts_{page}"),
            InlineKeyboardButton("👁️ معاينة", callback_data=f"view_{fatwa['id']}_drafts_{page}")
        ]
        keyboard.append(btns)

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_drafts_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_drafts:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"admin_drafts_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الفتاوى المكررة"""
    query = update.callback_query
    await query.answer()

    page = 0
    data = query.data or ""
    if data.startswith("admin_duplicates_"):
        try:
            page = max(0, int(data.rsplit("_", 1)[-1]))
        except ValueError:
            page = 0

    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    try:
        duplicates, total_count = await asyncio.gather(
            asyncio.to_thread(db.get_duplicate_fatwas, ITEMS_PER_PAGE, offset),
            asyncio.to_thread(db.get_duplicate_count),
        )
    except Exception:
        logger.exception("Failed to load duplicate fatwas")
        await query.edit_message_text(
            "❌ تعذر تحميل قائمة الفتاوى المكررة حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
        return

    if not duplicates and page == 0:
        await query.edit_message_text(
            "✅ لا توجد فتاوى مكررة حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
        return

    text = f"🔄 **الفتاوى المكررة (نفس الجواب)** (صفحة {page + 1})\nإجمالي المكرر: {total_count}\n\n"
    keyboard = []

    for fatwa in duplicates:
        # Title Button (acts as Label or View)
        keyboard.append([InlineKeyboardButton(f"🔸 {fatwa['title']}", callback_data=f"view_{fatwa['id']}_dups_{page}")])

        # Action Buttons
        btns = [
             InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa['id']}_dups_{page}"),
             InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa['id']}_dups_{page}")
        ]
        keyboard.append(btns)

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_duplicates_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"admin_duplicates_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==============================================================================
# 👥 القسم 2: إدارة المسؤولين (Admins)
# ==============================================================================

async def manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسؤولين مباشرة مع أزرار الإدارة"""
    await list_admins_handler(update, context)

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب بيانات المسؤول الجديد (ID أو username)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ **إضافة مسؤول**\n\nأرسل `User ID` أو `@username` للمستخدم:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")]]),
    )
    return STATE_ADMIN_ADD

async def receive_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    target_user_id = None
    target_username = None

    if text.isdigit():
        target_user_id = int(text)
        if target_user_id <= 0:
            await update.message.reply_text("❌ الآيدي غير صالح.")
            return STATE_ADMIN_ADD
    else:
        username_candidate = text[1:] if text.startswith("@") else text
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username_candidate):
            await update.message.reply_text("❌ أدخل `User ID` صحيح أو `@username` صحيح.", parse_mode="Markdown")
            return STATE_ADMIN_ADD

        user_row = bot_db.get_user_by_username(username_candidate)
        if user_row:
            target_user_id = int(user_row["user_id"])
            target_username = user_row.get("username") or username_candidate
        else:
            try:
                chat = await context.bot.get_chat(f"@{username_candidate}")
                if getattr(chat, "type", "") != "private":
                    await update.message.reply_text("❌ هذا اليوزر لا يشير إلى حساب مستخدم خاص.")
                    return STATE_ADMIN_ADD
                target_user_id = int(chat.id)
                target_username = chat.username or username_candidate
            except Exception:
                await update.message.reply_text(
                    "❌ تعذر العثور على هذا اليوزر.\nتأكد من صحة اليوزر وأن المستخدم بدأ البوت أولاً."
                )
                return STATE_ADMIN_ADD

    if bot_db.add_admin(target_user_id, target_username):
        if target_username:
            success_text = f"✅ تم إضافة المسؤول: @{html.escape(target_username)} | <code>{target_user_id}</code>"
        else:
            success_text = f"✅ تم إضافة المسؤول: <code>{target_user_id}</code>"
        await update.message.reply_text(
            success_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
    else:
        await update.message.reply_text(
            "⚠️ المستخدم مسؤول بالفعل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
    return ConversationHandler.END

async def list_admins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسؤولين"""
    query = update.callback_query
    await query.answer()

    admins = bot_db.get_admins()
    admins_sorted = sorted(
        admins,
        key=lambda row: (
            0 if int(row.get('user_id', 0)) == int(OWNER_ID) else 1,
            int(row.get('user_id', 0)),
        ),
    )
    title = f"الأدمن [{len(admins_sorted)}]"
    lines = [f"🧑‍💼 <b>{title}</b>", ""]

    if not admins_sorted:
        lines.append("لا يوجد أدمن مسجل حالياً.")
    else:
        for idx, row in enumerate(admins_sorted, start=1):
            username = f"@{html.escape(row['username'])}" if row.get('username') else "بدون يوزر"
            role = "المالك" if int(row.get('user_id', 0)) == int(OWNER_ID) else "أدمن"
            lines.append(f"{idx}. {username} | <code>{row['user_id']}</code> | {role} | نشط")

    text = "\n".join(lines)
    keyboard = [
        [
            InlineKeyboardButton("❌ حذف", callback_data="remove_admin"),
            InlineKeyboardButton("➕ إضافة", callback_data="add_admin"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
    ]
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

# Remove logic similar to Add (omitted for brevity, assume implemented in same pattern)
async def start_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚠️ ميزة حذف المسؤولين تحت التطوير حالياً.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_admins")]])
    )
    return ConversationHandler.END


admin_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_admin, pattern='^add_admin$'),
        CallbackQueryHandler(start_remove_admin, pattern='^remove_admin$')
    ],
    states={
        STATE_ADMIN_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_id)],
        STATE_ADMIN_REMOVE: [CallbackQueryHandler(start_remove_admin, pattern='^remove_admin_')] # Placeholder
    },
    fallbacks=[CallbackQueryHandler(admin_panel, pattern='^admin_panel$'), CommandHandler('cancel', admin_panel)]
)


# ==============================================================================
# 🎓 القسم 3: إدارة العلماء (Scholars)
# ==============================================================================

async def manage_scholars_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة خيارات إدارة العلماء"""
    query = update.callback_query
    await query.answer()

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("👤 قائمة العلماء", callback_data="scholars_list_0")],
        [InlineKeyboardButton("➕ إضافة عالم جديد", callback_data="scholar_add_start")],
        [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
    ]

    await query.edit_message_text(
        "👤 **إدارة العلماء**\n\nاختر من العمليات التالية:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END


async def show_scholars_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة العلماء في لوحة الإدارة"""
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return


    page = 0
    data = query.data
    if "scholars_list_" in data:
        page = int(data.split("_")[-1])

    ITEMS_PER_PAGE = 10
    offset = page * ITEMS_PER_PAGE

    scholars = db.get_scholars_with_ids(limit=ITEMS_PER_PAGE, offset=offset)
    total_count = db.get_scholars_count()

    if not scholars and page == 0:
        await query.edit_message_text(
            "📭 لا يوجد علماء مضافين حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")]])
        )
        return

    text = f"👤 **إدارة العلماء** (صفحة {page + 1})\nإجمالي العلماء: {total_count}\n\n"
    keyboard = []

    for s in scholars:
        keyboard.append([InlineKeyboardButton(s['name'], callback_data=f"scholar_view_{s['id']}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"scholars_list_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"scholars_list_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def view_scholar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض بيانات عالم محدد"""
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return


    scholar_id = int(query.data.split("_")[-1])
    scholar = db.get_scholar_by_id(scholar_id)
    if not scholar:
        await query.edit_message_text(
            "⚠️ لم يتم العثور على هذا العالم.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]])
        )
        return

    bio = scholar.get('biography') or "لا يوجد سيرة ذاتية."
    website = scholar.get('website') or "لا يوجد موقع رسمي."

    if len(bio) > 1000:
        bio = bio[:1000].rstrip() + "..."

    # Escape generated text to prevent Markdown parsing errors
    safe_name = escape_markdown(scholar['name'])
    safe_bio = escape_markdown(bio)
    safe_website = escape_markdown(website)

    text = (
        f"👤 *{safe_name}*\n\n"
        f"📝 *السيرة الذاتية:*\n{safe_bio}\n\n"
        f"🌐 *الموقع الرسمي:*\n{safe_website}"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ تعديل السيرة/الموقع", callback_data=f"scholar_bio_{scholar_id}")],
        [InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown',
        disable_web_page_preview=True
    )


async def start_add_scholar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة عالم جديد"""
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END


    await query.edit_message_text(
        "👤 **إضافة عالم جديد**\n\nالرجاء إرسال اسم العالم الكامل:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")]]),
        parse_mode='Markdown'
    )
    return STATE_SCHOLAR_ADD


async def receive_new_scholar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sanitize_input(update.message.text, max_length=200)
    if not name:
        await update.message.reply_text("⚠️ عذراً، يجب إرسال اسم العالم بشكل نصي.")
        return STATE_SCHOLAR_ADD

    scholar_id = db.add_scholar(name)
    if scholar_id:
        safe_name = escape_markdown(name)
        await update.message.reply_text(
            f"✅ تم إضافة العالم: *{safe_name}*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]]),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "⚠️ عذراً، هذا العالم موجود بالفعل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")]])
        )
    return ConversationHandler.END


async def start_add_scholar_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة/تعديل سيرة العالم"""
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END


    scholar_id = int(query.data.split("_")[-1])
    scholar = db.get_scholar_by_id(scholar_id)
    if not scholar:
        await query.edit_message_text(
            "⚠️ لم يتم العثور على هذا العالم.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]])
        )
        return ConversationHandler.END

    context.user_data['scholar_bio_id'] = scholar_id

    safe_name = escape_markdown(scholar['name'])
    await query.edit_message_text(
        f"📝 *تعديل السيرة الذاتية للعالم:* {safe_name}\n\nأرسل السيرة الذاتية الجديدة:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"scholar_view_{scholar_id}")]]),
        parse_mode='Markdown'
    )
    return STATE_SCHOLAR_BIO


async def receive_scholar_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scholar_id = context.user_data.get('scholar_bio_id')
    if not scholar_id:
        await update.message.reply_text("⚠️ حدث خطأ في البيانات.")
        return ConversationHandler.END

    bio = sanitize_input(update.message.text, max_length=3500)
    context.user_data['scholar_bio_text'] = bio

    keyboard = [
        [InlineKeyboardButton("✅ متابعة", callback_data="scholar_bio_done"), InlineKeyboardButton("❌ إلغاء", callback_data=f"scholar_view_{scholar_id}")]
    ]

    await update.message.reply_text(
        "✅ تم استلام السيرة الذاتية.\nالمرحلة التالية هي إضافة الموقع الرسمي.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return STATE_SCHOLAR_BIO_CONFIRM


async def confirm_scholar_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    scholar_id = context.user_data.get('scholar_bio_id')
    if not scholar_id:
        await query.edit_message_text("⚠️ حدث خطأ في البيانات.")
        return ConversationHandler.END

    await query.edit_message_text(
        "🌐 أرسل رابط الموقع الرسمي للعالم:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"scholar_view_{scholar_id}")]])
    )
    return STATE_SCHOLAR_WEBSITE


async def receive_scholar_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scholar_id = context.user_data.get('scholar_bio_id')
    bio = context.user_data.get('scholar_bio_text', '')
    website = sanitize_input(update.message.text, max_length=500)

    if not scholar_id:
        await update.message.reply_text("⚠️ حدث خطأ في البيانات.")
        return ConversationHandler.END

    db.update_scholar_bio_website(scholar_id, bio, website)
    context.user_data.pop('scholar_bio_id', None)
    context.user_data.pop('scholar_bio_text', None)

    await update.message.reply_text(
        "✅ تم تحديث بيانات العالم بنجاح.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]])
    )
    return ConversationHandler.END


# ==============================================================================
# 🎙️ القسم 5: البودكاست والإذاعة العامة (Podcast & Broadcast)
# ==============================================================================

def _build_podcast_template(message_text: str) -> str:
    content = (message_text or "").strip()
    if content:
        return f"📢 رسالة لكافة المستخدمين:\n\n{content}"
    return "📢 رسالة لكافة المستخدمين:"

def _podcast_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📩 راسل المطور", url="https://t.me/abulharith_imad"),
        InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
    ]])

async def _send_podcast_to_user(context: ContextTypes.DEFAULT_TYPE, user_id: int, message, broadcast_text: str, reply_markup: InlineKeyboardMarkup):
    if message.text:
        return await context.bot.send_message(chat_id=user_id, text=broadcast_text, reply_markup=reply_markup)

    if message.photo:
        return await context.bot.send_photo(chat_id=user_id, photo=message.photo[-1].file_id, caption=broadcast_text, reply_markup=reply_markup)
    if message.video:
        return await context.bot.send_video(chat_id=user_id, video=message.video.file_id, caption=broadcast_text, reply_markup=reply_markup)
    if message.voice:
        return await context.bot.send_voice(chat_id=user_id, voice=message.voice.file_id, caption=broadcast_text, reply_markup=reply_markup)
    if message.audio:
        return await context.bot.send_audio(chat_id=user_id, audio=message.audio.file_id, caption=broadcast_text, reply_markup=reply_markup)
    if message.document:
        return await context.bot.send_document(chat_id=user_id, document=message.document.file_id, caption=broadcast_text, reply_markup=reply_markup)
    if message.animation:
        return await context.bot.send_animation(chat_id=user_id, animation=message.animation.file_id, caption=broadcast_text, reply_markup=reply_markup)
    if message.video_note:
        # video_note لا يدعم caption، نرسل النص كرسالة منفصلة
        await context.bot.send_message(chat_id=user_id, text=broadcast_text, reply_markup=reply_markup)
        return await context.bot.send_video_note(chat_id=user_id, video_note=message.video_note.file_id)

    # fallback: أرسل النص النموذجي مع الأزرار، ثم انسخ الرسالة كما هي
    await context.bot.send_message(chat_id=user_id, text=broadcast_text, reply_markup=reply_markup)
    return await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=message.chat_id,
        message_id=message.message_id
    )

async def podcast_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة التحكم بالبودكاست/الإذاعة"""
    query = update.callback_query
    await query.answer()

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END

    text = (
        "🎙️ **البودكاست / الإذاعة**\n\n"
        "يمكنك إرسال (صوت، فيديو، نص) لإرساله لجميع مستخدمي البوت (الإذاعة).\n"
        "سيتم إرسال المحتوى لجميع المشتركين النشطين."
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]]),
        parse_mode='Markdown'
    )
    return STATE_PODCAST_CONTENT


async def receive_podcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام محتوى البودكاست وبدء الإذاعة لجميع المشتركين"""
    if not bot_db.is_admin(update.effective_user.id):
        await update.message.reply_text("❌ غير مصرح لك.")
        return ConversationHandler.END

    message = update.message
    if not message:
        return ConversationHandler.END

    users = bot_db.get_active_user_ids()
    total_targets = len(users)

    if total_targets == 0:
        await update.message.reply_text("⚠️ لا يوجد مشتركون حالياً.")
        return ConversationHandler.END

    broadcast_id = f"{update.effective_user.id}_{int(datetime.now().timestamp())}"
    store = context.bot_data.setdefault('podcast_broadcasts', {})
    store[broadcast_id] = {'cancel': False}

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية", callback_data=f"podcast_cancel_{broadcast_id}")]])
    await update.message.reply_text("🔄 جاري بدء عملية الإرسال...", reply_markup=cancel_btn)

    report = {'success': 0, 'fail': 0}
    canceled = False
    podcast_markup = _podcast_inline_keyboard()
    broadcast_text = _build_podcast_template(message.text or message.caption or "")
    sent_messages = []

    for user_id in users:
        if store.get(broadcast_id, {}).get('cancel'):
            canceled = True
            break
        try:
            msg = await _send_podcast_to_user(context, user_id, message, broadcast_text, podcast_markup)
            if msg:
                # copy_message returns a MessageId object which has message_id,
                # send_message returns a Message object which has message_id
                msg_id = getattr(msg, 'message_id', None)
                if msg_id:
                    sent_messages.append({"chat_id": user_id, "message_id": msg_id})
            report['success'] += 1
        except Exception as e:
            report['fail'] += 1
            err = str(e).lower()
            if 'forbidden' in err or 'bot was blocked' in err or 'user is deactivated' in err:
                bot_db.set_user_blocked(user_id, True)

    store.pop(broadcast_id, None)
    
    # حفظ الرسائل المرسلة لتعديلها أو حذفها
    context.user_data["podcast_sent_messages"] = sent_messages

    status_line = "🛑 تم إيقاف الإرسال بواسطة المسؤول." if canceled else "✅ اكتملت عملية الإرسال بنجاح."
    summary = (
        f"{status_line}\n\n"
        f"✅ تم الإرسال بنجاح لـ: {report['success']}\n"
        f"❌ فشل الإرسال لـ: {report['fail']}\n"
        f"👥 إجمالي المستهدفين: {total_targets}"
    )

    keyboard = []
    if sent_messages:
        keyboard.append([
            InlineKeyboardButton("✏️ تعديل الإذاعة", callback_data="podcast_edit_bc"),
            InlineKeyboardButton("🗑️ حذف الإذاعة", callback_data="podcast_del_bc")
        ])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")])

    await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


async def cancel_podcast_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية الإرسال الجارية"""
    query = update.callback_query
    await query.answer()

    broadcast_id = query.data.replace("podcast_cancel_", "")
    store = context.bot_data.get('podcast_broadcasts', {})
    if broadcast_id in store:
        store[broadcast_id]['cancel'] = True
        await query.message.reply_text("🛑 تم طلب إيقاف العملية، قد يستمر الإرسال لثوانٍ إضافية.")
    else:
        await query.answer("⚠️ العملية منتهية بالفعل.", show_alert=True)

async def start_edit_podcast_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء تعديل الإذاعة"""
    query = update.callback_query
    await query.answer()
    
    sent_messages = context.user_data.get("podcast_sent_messages")
    if not sent_messages:
        await query.answer("⚠️ لا توجد رسائل لتعديلها.", show_alert=True)
        return ConversationHandler.END
        
    await query.edit_message_text(
        "✏️ **تعديل الإذاعة**\n\nأرسل النص الجديد الذي تريد استبداله بالرسائل المرسلة:\n\n*(ملاحظة: يمكنك إرسال نص فقط للاستبدال)*",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")]]),
        parse_mode="Markdown"
    )
    return STATE_PODCAST_EDIT

async def receive_podcast_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام النص الجديد وتعديل الرسائل"""
    message = update.message
    if not message.text:
        await message.reply_text("⚠️ يرجى إرسال نص فقط للتعديل.")
        return STATE_PODCAST_EDIT
        
    sent_messages = context.user_data.get("podcast_sent_messages", [])
    if not sent_messages:
        await message.reply_text("⚠️ لم يتم العثور على رسائل لتعديلها.")
        return ConversationHandler.END
        
    new_text = _build_podcast_template(message.text)
    podcast_markup = _podcast_inline_keyboard()
    
    msg = await message.reply_text("⏳ جاري تعديل الرسائل...")
    success = 0
    fail = 0
    
    for sent in sent_messages:
        try:
            await context.bot.edit_message_text(
                chat_id=sent["chat_id"],
                message_id=sent["message_id"],
                text=new_text,
                reply_markup=podcast_markup
            )
            success += 1
        except Exception as e:
            fail += 1
            
    await msg.edit_text(
        f"✅ تم تعديل الرسائل بنجاح.\n\nنجاح: {success}\nفشل: {fail}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]])
    )
    return ConversationHandler.END

async def delete_podcast_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف الإذاعة المرسلة"""
    query = update.callback_query
    await query.answer()
    
    sent_messages = context.user_data.get("podcast_sent_messages", [])
    if not sent_messages:
        await query.answer("⚠️ لا توجد رسائل لحذفها.", show_alert=True)
        return ConversationHandler.END
        
    await query.edit_message_text("⏳ جاري حذف الرسائل...")
    success = 0
    fail = 0
    
    for sent in sent_messages:
        try:
            await context.bot.delete_message(
                chat_id=sent["chat_id"],
                message_id=sent["message_id"]
            )
            success += 1
        except Exception as e:
            fail += 1
            
    # تنظيف
    context.user_data.pop("podcast_sent_messages", None)
    
    await query.edit_message_text(
        f"🗑️ تم حذف الرسائل بنجاح.\n\nنجاح: {success}\nفشل: {fail}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]])
    )
    return ConversationHandler.END


# ==============================================================================
# 👥 القسم 6: إدارة المشتركين (Subscribers Management)
# ==============================================================================

async def manage_subscribers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode_override: str | None = None,
    page_override: int | None = None,
):
    """عرض قائمة المشتركين النشطين"""
    query = update.callback_query
    await query.answer()
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return


    mode = "active"
    page = 0
    if mode_override in {"active", "inactive"}:
        mode = mode_override
        try:
            page = int(page_override) if page_override is not None else 0
        except (TypeError, ValueError):
            page = 0
    else:
        data = query.data
        if data.startswith("manage_subscribers_"):
            tail = data.replace("manage_subscribers_", "", 1)
            parts = [part for part in tail.split("_") if part]
            if parts:
                if parts[0] in {"active", "inactive"}:
                    mode = parts[0]
                    if len(parts) > 1:
                        try:
                            page = int(parts[1])
                        except (TypeError, ValueError):
                            page = 0
                else:
                    # توافق مع الصيغة القديمة: manage_subscribers_{page}
                    try:
                        page = int(parts[0])
                    except (TypeError, ValueError):
                        page = 0

    ITEMS_PER_PAGE = 10
    if mode == "inactive":
        total_count = bot_db.get_inactive_users_count()
    else:
        total_count = bot_db.get_active_users_count()

    total_pages = max((total_count - 1) // ITEMS_PER_PAGE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    offset = page * ITEMS_PER_PAGE

    if mode == "inactive":
        users = bot_db.get_inactive_users(limit=ITEMS_PER_PAGE, offset=offset)
    else:
        users = bot_db.get_active_users(limit=ITEMS_PER_PAGE, offset=offset)

    admin_ids = {int(a['user_id']) for a in bot_db.get_admins()}
    admin_ids.add(int(OWNER_ID))

    if total_count == 0:
        title = "المستخدمون غير النشطين [0]" if mode == "inactive" else "المستخدمون النشطين [0]"
        toggle_mode = "active" if mode == "inactive" else "inactive"
        toggle_label = "✅ النشطة" if mode == "inactive" else "⚫ غير النشطة"
        await query.edit_message_text(
            f"🙍 <b>{title}</b>\n\nلا يوجد مستخدمون في هذه القائمة.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⬅️ السابق", callback_data="noop"),
                    InlineKeyboardButton(toggle_label, callback_data=f"manage_subscribers_{toggle_mode}_0"),
                    InlineKeyboardButton("➡️ التالي", callback_data="noop"),
                ],
                [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
            ]),
            parse_mode='HTML'
        )
        return

    state_label = "غير النشطين" if mode == "inactive" else "النشطين"
    user_state = "غير نشط" if mode == "inactive" else "نشط"
    lines = [
        f"🙍 <b>المستخدمون {state_label} [{total_count}]</b>",
        f"الصفحة: {page + 1}/{total_pages}",
        "",
    ]

    for idx, user_row in enumerate(users, start=offset + 1):
        full_name = html.escape(user_row.get('full_name') or '-')
        username = f"@{html.escape(user_row['username'])}" if user_row.get('username') else "بدون يوزر"
        role = "أدمن" if int(user_row['user_id']) in admin_ids else "مستخدم"
        lines.append(
            f"{idx}. {full_name} | {username} | <code>{user_row['user_id']}</code> | {role} | {user_state}"
        )

    text = "\n".join(lines)
    prev_page_cb = f"manage_subscribers_{mode}_{page - 1}" if page > 0 else "noop"
    next_page_cb = f"manage_subscribers_{mode}_{page + 1}" if page < total_pages - 1 else "noop"
    toggle_mode = "active" if mode == "inactive" else "inactive"
    toggle_label = "✅ النشطة" if mode == "inactive" else "⚫ غير النشطة"
    keyboard = [[
        InlineKeyboardButton("⬅️ السابق", callback_data=prev_page_cb),
        InlineKeyboardButton(toggle_label, callback_data=f"manage_subscribers_{toggle_mode}_0"),
        InlineKeyboardButton("➡️ التالي", callback_data=next_page_cb),
    ]]
    if mode == "inactive" and total_count:
        keyboard.append([InlineKeyboardButton("🗑️ حذف غير النشطين (إزالة البوت)", callback_data="cleanup_subscribers")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ==============================================================================
# 📂 القسم 4: إدارة التصنيفات والمواضيع (Categories & Topics)
# ==============================================================================



def _is_user_removed_error(error: Exception) -> bool:
    err = str(error).lower()
    removal_tokens = (
        "forbidden",
        "bot was blocked",
        "user is deactivated",
        "chat not found",
    )
    return any(token in err for token in removal_tokens)


async def cleanup_inactive_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clean up inactive subscribers that actually removed the bot."""
    query = update.callback_query
    await query.answer("جاري التنظيف...", cache_time=0)

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    users = bot_db.get_inactive_users()

    removed_count = 0
    reactivated_count = 0
    kept_count = 0

    for user_row in users:
        user_id = int(user_row["user_id"])
        try:
            await context.bot.send_chat_action(chat_id=user_id, action="typing")
            bot_db.set_user_blocked(user_id, False)
            reactivated_count += 1
        except Exception as e:
            if _is_user_removed_error(e):
                if bot_db.remove_user(user_id):
                    removed_count += 1
                else:
                    kept_count += 1
            else:
                kept_count += 1
                logger.debug(f"Skipping cleanup for user {user_id}; could not verify removal state: {e}")

    await query.message.reply_text(
        "✅ اكتمل تنظيف المستخدمين غير النشطين.\n"
        f"🗑️ المحذوف (حظر/تعطيل فعلي): {removed_count}\n"
        f"🔄 عاد للنشاط تلقائيًا: {reactivated_count}\n"
        f"📌 تم الإبقاء عليه (تعذر التحقق): {kept_count}"
    )

    await manage_subscribers(update, context, mode_override="inactive", page_override=0)


async def manage_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة التصنيفات: شبكة ثنائية، 10 عناصر، بحث"""
    query = update.callback_query
    await query.answer()

    # استخراج رقم الصفحة
    page = 0
    data = query.data
    if "page_" in data:
        page = int(data.split("page_")[-1])

    ITEMS_PER_PAGE = 10
    offset = page * ITEMS_PER_PAGE

    # التحقق من وجود بحث أو فلترة نوع
    search_query = context.user_data.get('admin_cat_search_query')
    cat_type = context.user_data.get('admin_cat_type') # 'fiqh', 'topic', or None (All)

    # جلب البيانات
    categories = db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type)
    total_count = db.get_categories_count(search_query=search_query, category_type=cat_type)

    # بناء الأزرار
    keyboard = []

    # الصف العلوي: البحث والإلغاء
    top_row = []
    if search_query:
        top_row.append(InlineKeyboardButton("❌ إلغاء البحث", callback_data="admin_search_cat_cancel"))
    else:
        top_row.append(InlineKeyboardButton("🔍 بحث", callback_data="admin_search_cat_start"))

    keyboard.append(top_row)

    # صف الفلاتر والأنواع
    type_row = [
        InlineKeyboardButton("📋 الكل" if cat_type is None else "الكل", callback_data="admin_cat_type_all"),
        InlineKeyboardButton("🕌 فقهي" if cat_type == 'fiqh' else "فقهي", callback_data="admin_cat_type_fiqh"),
        InlineKeyboardButton("📂 موضوعي" if cat_type == 'topic' else "موضوعي", callback_data="admin_cat_type_topic")
    ]
    keyboard.append(type_row)

    # صف الإضافة
    add_row = [
        InlineKeyboardButton("➕ تصنيف فقهي", callback_data="add_cat_fiqh"),
        InlineKeyboardButton("➕ تصنيف موضوعي", callback_data="add_cat_topic")
    ]
    keyboard.append(add_row)

    # بناء الشبكة الثنائية
    if categories:
        grid_rows = []
        row = []
        for cat_id, name in categories:
            btn = InlineKeyboardButton(f"📂 {name}", callback_data=f"view_topics_cat_{cat_id}")
            row.append(btn)
            if len(row) == 2:
                grid_rows.append(row)
                row = []
        if row: # العنصر الأخير الفردي
            grid_rows.append(row)

        keyboard.extend(grid_rows)
    else:
        keyboard.append([InlineKeyboardButton("🚫 لا توجد تصنيفات", callback_data="noop")])

    # أزرار التنقل (متقابلة)
    nav_buttons = []
    # زر السابق (يمين لأنه عربي)
    prev_btn = InlineKeyboardButton("⬅️ السابق", callback_data=f"manage_categories_page_{page-1}")
    # زر التالي (يسار)
    next_btn = InlineKeyboardButton("➡️ التالي", callback_data=f"manage_categories_page_{page+1}")

    # في تيليجرام الترتيب يسار->يمين، لذا الأول يسار والثاني يمين.
    # لغة عربية: نريد [التالي] [السابق] لكي يظهر التالي على اليسار والسابق على اليمين؟
    # عادة: [<< Prev] [Next >>]
    # لنجعلها: [السابق] [التالي]

    nav_row = []
    if page > 0:
        nav_row.append(prev_btn)

    if offset + ITEMS_PER_PAGE < total_count:
        # إذا كان زر السابق موجود، نضعهما في صف واحد
        # إذا لم يكن، زر التالي لوحده
        nav_row.insert(0, next_btn) # نضيف التالي في البداية ليظهر على اليسار؟
        # لحظة، القوائم العربية تبدأ من اليمين في العرض؟ لا، Telegram buttons are LTR by default layout.
        # [Btn1] [Btn2] -> Btn1 Left, Btn2 Right.
        # We want: [Next] [Prev] -> Next (Left), Prev (Right)? Or standard [Prev] [Next]?
        # User said: "ازرار الانتقال متقابلة وفوق قائمة التصنيفات متقابلة ايضا" ???
        # "فوق قائمة التصنيفات متقابلة ايضا" -> maybe he means pagination ON TOP as well?
        # Let's stick to BOTTOM pagination for now, standard [Prev] [Next] (Prev Left, Next Right) is standard.
        # User said "متقابلة". I'll put them in one row [Prev, Next].
        pass

    # Re-logic for nav:
    real_nav_row = []
    if page > 0:
        real_nav_row.append(prev_btn)
    if offset + ITEMS_PER_PAGE < total_count:
        real_nav_row.append(next_btn)

    if real_nav_row:
        keyboard.append(real_nav_row)

    # أزرار التحكم السفلية
    keyboard.append([InlineKeyboardButton("🔙 رجوع للوحة", callback_data="admin_panel")])

    type_label = ""
    if cat_type == 'fiqh': type_label = " (فقهي)"
    elif cat_type == 'topic': type_label = " (موضوعي)"

    title_suffix = f" {type_label}" + (f" (بحث: {search_query})" if search_query else "")
    text = f"🏷️ **إدارة التصنيفات** (صفحة {page + 1}){title_suffix}\nإجمالي التصنيفات: {total_count}"

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END


async def start_add_category_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "add_cat_topic":
        context.user_data['temp_cat_type'] = 'topic'
        label = "الموضوعي"
    else:
        context.user_data['temp_cat_type'] = 'fiqh'
        label = "الفقهي"

    await query.edit_message_text(
        f"🏷️ أرسل اسم التصنيف **{label}** الجديد:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="manage_categories")]])
    )
    return STATE_CATEGORY_ADD


async def handle_category_type_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "admin_cat_type_all":
        context.user_data['admin_cat_type'] = None
    elif data == "admin_cat_type_fiqh":
        context.user_data['admin_cat_type'] = 'fiqh'
    elif data == "admin_cat_type_topic":
        context.user_data['admin_cat_type'] = 'topic'

    return await manage_categories(update, context)


async def receive_new_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sanitize_input(update.message.text)
    cat_type = context.user_data.get('temp_cat_type', 'fiqh')

    if db.add_category(name, category_type=cat_type):
        await update.message.reply_text(
            f"✅ تم إضافة التصنيف: {name}",
            reply_markup=back_to_categories_keyboard("🔙 إدارة التصنيفات")
        )
    else:
        await update.message.reply_text(
            f"⚠️ التصنيف '{name}' موجود مسبقاً.",
            reply_markup=back_to_categories_keyboard("🔙 إدارة التصنيفات")
        )
    return ConversationHandler.END

# Search Handlers
async def start_search_category_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 **بحث في التصنيفات**\nأرسل اسم التصنيف للبحث عنه:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="manage_categories")]])
    )
    return STATE_ADMIN_SEARCH_CAT

async def receive_category_search_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    context.user_data['admin_cat_search_query'] = query_text

    # عرض النتائج مباشرة
    await manage_categories(update, context)
    return ConversationHandler.END

async def cancel_category_search_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['admin_cat_search_query'] = None
    return await manage_categories(update, context)

# Edit Category Handlers
async def start_edit_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.split('edit_category_')[-1])
    context.user_data['edit_category_id'] = cat_id

    await query.edit_message_text(
        "📝 **تعديل اسم التصنيف**\nأرسل الاسم الجديد:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"view_topics_cat_{cat_id}")]])
    )
    return STATE_CATEGORY_EDIT

async def receive_edit_category_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = sanitize_input(update.message.text)
    cat_id = context.user_data.get('edit_category_id')

    if not cat_id:
        await update.message.reply_text("❌ حدث خطأ في النظام.", reply_markup=back_to_categories_keyboard())
        return ConversationHandler.END

    if db.update_category(cat_id, new_name):
        await update.message.reply_text(
            f"✅ تم تحديث اسم التصنيف إلى: **{new_name}**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عرض المواضيع", callback_data=f"view_topics_cat_{cat_id}")]])
        )
    else:
        await update.message.reply_text("⚠️ فشل التحديث (قد يكون الاسم مستخدماً).", reply_markup=back_to_categories_keyboard())

    return ConversationHandler.END

# Category conv must include search and edit states
category_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_category_admin, pattern='^add_cat_(fiqh|topic)$'),
        CallbackQueryHandler(start_search_category_admin, pattern='^admin_search_cat_start$'),
        CallbackQueryHandler(start_edit_category_name, pattern='^edit_category_'),
        CallbackQueryHandler(cancel_category_search_admin, pattern='^admin_search_cat_cancel$')
    ],
    states={
        STATE_CATEGORY_ADD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_category)
        ],
        STATE_ADMIN_SEARCH_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category_search_term)],
        STATE_CATEGORY_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_category_name)]
    },
    fallbacks=[CallbackQueryHandler(manage_categories, pattern='^manage_categories')]
)

# ==================== إدارة المواضيع ====================

# ==================== إدارة المواضيع ====================

async def start_add_topic_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # data format: add_topic_cat_{id}
    cat_id = int(query.data.split('_cat_')[-1])
    context.user_data['add_topic_cat_id'] = cat_id

    await query.edit_message_text("أرسل اسم الموضوع الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"view_topics_cat_{cat_id}")]]))
    return STATE_TOPIC_ADD

async def receive_new_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sanitize_input(update.message.text)
    cat_id = context.user_data.get('add_topic_cat_id')

    if not cat_id:
        await update.message.reply_text(
            "❌ خطأ في تحديد التصنيف.",
            reply_markup=back_to_categories_keyboard("🔙 إدارة التصنيفات")
        )
        return ConversationHandler.END

    if db.add_topic(name, cat_id):
        await update.message.reply_text(f"✅ تم إضافة الموضوع: {name}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عرض المواضيع", callback_data=f"view_topics_cat_{cat_id}")]]))
    else:
        await update.message.reply_text(f"⚠️ الموضوع '{name}' موجود مسبقاً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 عرض المواضيع", callback_data=f"view_topics_cat_{cat_id}")]]))
    return ConversationHandler.END

async def view_topics_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض مواضيع تصنيف معين مع Pagination وشبكة ثنائية"""
    query = update.callback_query
    await query.answer()

    data = query.data
    # المتوقع: view_topics_cat_{id} أو view_topics_cat_{id}_page_{p}

    try:
        if "_page_" in data:
            base_part, page_part = data.split("_page_")
            cat_id = int(base_part.split("_cat_")[-1])
            page = int(page_part)
        else:
            cat_id = int(data.split("_cat_")[-1])
            page = 0

        ITEMS_PER_PAGE = 10
        offset = page * ITEMS_PER_PAGE

        topics = db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset)
        total_count = db.get_topics_count(cat_id)

        # نحتاج اسم التصنيف للعرض
        all_cats = dict(db.get_categories())
        cat_name = all_cats.get(cat_id, "غير معروف")

        keyboard = []

        # أزرار الإضافة والتعديل (متقابلة)
        action_row = [
            InlineKeyboardButton("➕ موضوع جديد", callback_data=f"add_topic_cat_{cat_id}"),
            InlineKeyboardButton("✏️ تعديل التصنيف", callback_data=f"edit_category_{cat_id}")
        ]
        keyboard.append(action_row)
        keyboard.append([InlineKeyboardButton("🗑️ حذف التصنيف", callback_data=f"confirm_delete_category_{cat_id}")])

        # عرض المواضيع في شبكة ثنائية
        if topics:
            grid_rows = []
            row = []
            for tid, name in topics:
                # جعل المواضيع قابلة للضغط للإدارة
                btn = InlineKeyboardButton(f"• {name}", callback_data=f"manage_topic_{tid}")
                row.append(btn)
                if len(row) == 2:
                    grid_rows.append(row)
                    row = []
            if row:
                grid_rows.append(row)
            keyboard.extend(grid_rows)
        else:
            keyboard.append([InlineKeyboardButton("🚫 لا توجد مواضيع", callback_data="noop")])

        # التنقل (متقابلة)
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"view_topics_cat_{cat_id}_page_{page-1}"))

        if offset + ITEMS_PER_PAGE < total_count:
            nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"view_topics_cat_{cat_id}_page_{page+1}"))

        if nav_row:
            keyboard.append(nav_row)

        keyboard.append([InlineKeyboardButton("🔙 رجوع للتصنيفات", callback_data="manage_categories")])

        await query.edit_message_text(
            f"📂 **المواضيع في: {cat_name}**\n(صفحة {page + 1} - المجموع {total_count})",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )

    except Exception as e:
        logger.error(f"Error viewing topics: {e}")
        await query.edit_message_text("❌ خطأ في عرض المواضيع", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_categories")]]))
    return ConversationHandler.END


async def manage_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض خيارات إدارة موضوع معين"""
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.split('_')[-1])
    topic = db.get_topic(topic_id)

    if not topic:
        await query.answer("❌ الموضوع غير موجود.", show_alert=True)
        return

    cat_id = topic['category_id']

    keyboard = [
        [
            InlineKeyboardButton("✏️ تعديل الموضوع", callback_data=f"edit_topic_{topic_id}"),
            InlineKeyboardButton("🗑️ حذف الموضوع", callback_data=f"confirm_delete_topic_{topic_id}")
        ],
        [InlineKeyboardButton("🔙 رجوع للخلف", callback_data=f"view_topics_cat_{cat_id}")]
    ]

    await query.edit_message_text(
        f"📑 **إدارة الموضوع: {topic['name']}**\n\nاختر العملية المطلوبة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def start_edit_topic_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.split('_')[-1])
    context.user_data['edit_topic_id'] = topic_id

    topic = db.get_topic(topic_id)
    await query.edit_message_text(
        f"📝 **تعديل الموضوع: {topic['name']}**\nأرسل الاسم الجديد:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"manage_topic_{topic_id}")]])
    )
    return STATE_TOPIC_EDIT

async def receive_edit_topic_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = sanitize_input(update.message.text)
    topic_id = context.user_data.get('edit_topic_id')

    if not topic_id:
        await update.message.reply_text("❌ حدث خطأ، حاول مرة أخرى.")
        return ConversationHandler.END

    topic = db.get_topic(topic_id)
    cat_id = topic['category_id']

    if db.update_topic(topic_id, new_name):
        await update.message.reply_text(
            f"✅ تم تحديث اسم الموضوع إلى: **{new_name}**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للمواضيع", callback_data=f"view_topics_cat_{cat_id}")]])
        )
    else:
        await update.message.reply_text("⚠️ فشل التحديث (قد يكون الاسم مستخدماً).")

    return ConversationHandler.END

async def confirm_delete_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.split('_')[-1])
    topic = db.get_topic(topic_id)

    keyboard = [
        [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"delete_topic_{topic_id}")],
        [InlineKeyboardButton("❌ تراجع", callback_data=f"manage_topic_{topic_id}")]
    ]

    await query.edit_message_text(
        f"⚠️ **تأكيد الحذف**\n\nهل أنت متأكد من حذف الموضوع: **{topic['name']}**؟\nسيتم فك ارتباطه بجميع الفتاوى.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def delete_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.split('_')[-1])
    topic = db.get_topic(topic_id) # جلب البيانات قبل الحذف لمعرفة التصنيف
    cat_id = topic['category_id']

    if db.delete_topic(topic_id):
        await query.edit_message_text(
            f"✅ تم حذف الموضوع: **{topic['name']}** بنجاح.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للمواضيع", callback_data=f"view_topics_cat_{cat_id}")]])
        )
    else:
        await query.edit_message_text("❌ فشل حذف الموضوع.")

async def confirm_delete_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.split('_')[-1])
    category = db.get_category(cat_id)
    if not category:
        await query.edit_message_text(
            "❌ التصنيف غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_categories")]])
        )
        return

    topics_count = db.get_topics_count(cat_id)
    keyboard = [
        [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"delete_category_{cat_id}")],
        [InlineKeyboardButton("❌ تراجع", callback_data=f"view_topics_cat_{cat_id}")]
    ]

    await query.edit_message_text(
        f"⚠️ **تأكيد الحذف**\n\nهل أنت متأكد من حذف التصنيف: **{category['name']}**؟\n"
        f"سيتم حذف {topics_count} موضوع وفك ارتباطه بجميع الفتاوى.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def delete_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.split('_')[-1])
    category = db.get_category(cat_id)
    if not category:
        await query.edit_message_text(
            "❌ التصنيف غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_categories")]])
        )
        return

    if db.delete_category(cat_id):
        await query.edit_message_text(
            f"✅ تم حذف التصنيف: **{category['name']}** بنجاح.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة التصنيفات", callback_data="manage_categories")]]),
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text(
            "❌ فشل حذف التصنيف.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"view_topics_cat_{cat_id}")]])
        )


# ==============================================================================
# 📊 القسم 7: الإحصائيات والنسخ الاحتياطي (Stats & Backup)
# ==============================================================================

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الإحصائيات"""
    query = update.callback_query
    await query.answer()

    stats = db.get_statistics()
    bot_stats = bot_db.get_statistics()
    stats.update(bot_stats)

    # عدّاد "المضافة هذا الأسبوع" يبدأ من فجر الجمعة (التوقيت المحلي).
    local_now = datetime.now().astimezone()
    days_since_friday = (local_now.weekday() - 4) % 7  # Friday = 4
    friday_start_local = (local_now - timedelta(days=days_since_friday)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    friday_start_utc = friday_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    weekly_added_count = db.count_fatwas_since(friday_start_utc.strftime("%Y-%m-%d %H:%M:%S"))

    daily_time = bot_db.get_setting("daily_publish_time", "12:00") or "12:00"
    weekly_time = bot_db.get_setting("weekly_report_time", "08:00") or "08:00"
    weekly_day_raw = bot_db.get_setting("weekly_report_weekday", "4") or "4"
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
        f"🆕 المضافة هذا الأسبوع: {weekly_added_count}\n"
        f"🏷️ التصنيفات: {stats.get('categories', 0)}\n"
        f"👤 عداد المشايخ: {stats.get('scholars', 0)}\n\n"
        f"📢 القنوات: {stats.get('channels', 0)}\n"
        f"👥 المجموعات: {stats.get('groups', 0)}\n"
        f"👤 المشتركين: {stats.get('subscribers', 0)}\n\n"
        f"⏰ وقت النشر اليومي: {daily_time}\n"
        f"📅 موعد التقرير الأسبوعي: {weekly_day_name} - {weekly_time}\n\n"
    )

    # إضافة أكثر الفتاوى تفضيلاً
    top_favs = db.get_top_favorites(5)
    if top_favs:
        text_out += "⭐ **أكثر الفتاوى تفضيلاً:**\n"
        for i, fav in enumerate(top_favs, 1):
            text_out += f"{i}. {fav['title']} ({fav['fav_count']})\n"

    keyboard = [
        [InlineKeyboardButton("🔄 تحديث", callback_data="stats"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
    ]
    try:
        await query.edit_message_text(text_out, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except BadRequest as e:
        if "Message is not modified" in str(e):
            await query.answer("⚠️ البيانات محدثة بالفعل", show_alert=False)
        else:
            raise e

async def backup_database_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إنشاء نسخة احتياطية (DB + JSON)"""
    query = update.callback_query
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return
    await query.answer("جاري النسخ...")

    # اسم الملف مع التاريخ
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_filename = f"fatwa_backup_{timestamp}.db"
    json_filename = f"fatwa_backup_{timestamp}.json"

    db_path = os.path.join(BACKUP_DIR, db_filename)
    json_path = os.path.join(BACKUP_DIR, json_filename)

    success_db = db.backup_database(db_path)
    success_json = db.export_json(json_path)

    if success_db:
        with open(db_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                caption=f"✅ نسخة قاعدة البيانات: {timestamp}"
            )

    if success_json:
        with open(json_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                caption=f"✅ نسخة JSON: {timestamp}"
            )

    if not success_db and not success_json:
        await query.edit_message_text("❌ فشل النسخ الاحتياطي بالكامل.")
    elif not success_db:
        await query.message.reply_text("⚠️ فشل نسخ قاعدة البيانات (تم نسخ JSON فقط).")
    elif not success_json:
        await query.message.reply_text("⚠️ فشل نسخ JSON (تم نسخ قاعدة البيانات فقط).")

# ==================== إدارة الروابط (Missing Links) ====================

async def manage_links_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة إدارة الروابط"""
    query = update.callback_query
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("🔗 روابط مصدر ناقصة", callback_data="missing_links_source")],
        [InlineKeyboardButton("🔊 روابط صوتية ناقصة", callback_data="missing_links_audio")],
        [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
    ]

    await query.edit_message_text(
        "🔗 **إدارة الروابط**\n\nاختر نوع الروابط التي تريد مراجعتها:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_missing_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الفتاوى الناقصة روابط"""
    query = update.callback_query
    if not bot_db.is_admin(update.effective_user.id):
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

    fatwas = db.get_fatwas_missing_link(link_type, limit=ITEMS_PER_PAGE, offset=offset)
    total_count = db.get_missing_link_count(link_type)

    link_label = "مصدر" if link_type == 'source' else "صوتية"

    if not fatwas and page == 0:
        await query.edit_message_text(
            f"✅ لا توجد فتاوى ينقصها روابط {link_label}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة الروابط", callback_data="manage_links")]])
        )
        return

    text = f"🔗 **فتاوى ينقصها روابط {link_label}** (صفحة {page + 1})\nإجمالي العدد: {total_count}\n\n"
    keyboard = []

    for fatwa in fatwas:
        # Title Button (to view/edit)
        keyboard.append([InlineKeyboardButton(f"🔸 {fatwa['title']}", callback_data=f"view_{fatwa['id']}_missing_{link_type}_{page}")])

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"missing_links_{link_type}_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"missing_links_{link_type}_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 إدارة الروابط", callback_data="manage_links")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# تعريف topic_conv في نهاية الملف لضمان تعريف جميع المعالجات المستخدمة


# ==============================================================================
# 🔗 القسم 8: إدارة المصادر والروابط (Sources & Links)
# ==============================================================================

async def manage_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المصادر مع Pagination"""
    query = update.callback_query
    await query.answer()

    page = 0
    data = query.data
    if "manage_sources_page_" in data:
        page = int(data.split("manage_sources_page_")[-1])

    ITEMS_PER_PAGE = 10
    offset = page * ITEMS_PER_PAGE

    sources = db.get_sources(limit=ITEMS_PER_PAGE, offset=offset)
    total_count = db.get_sources_count()

    keyboard = []
    keyboard.append([InlineKeyboardButton("➕ إضافة مصدر", callback_data="add_source")])

    if sources:
        row = []
        for s_id, s_name in sources: # Adjusted to match db.get_sources return type
             name = s_name
             row.append(InlineKeyboardButton(f"📚 {name}", callback_data=f"manage_source_{s_id}")) # Adjusted to match db.get_sources return type
             if len(row) == 2:
                 keyboard.append(row)
                 row = []
        if row:
            keyboard.append(row)
    else:
        keyboard.append([InlineKeyboardButton("❌ لا توجد مصادر حالياً", callback_data="manage_sources")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"manage_sources_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"manage_sources_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(
        "📚 **إدارة المصادر**\n\nاختر مصدرًا لإدارته:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def manage_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    source_id = int(query.data.split("_")[-1])
    source = db.get_source(source_id)
    if not source:
        await query.edit_message_text(
            "❌ المصدر غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_sources")]])
        )
        return

    titles_count = db.get_source_titles_count(source_id)

    keyboard = [
        [InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"edit_source_{source_id}")],
        [InlineKeyboardButton("🗑️ حذف المصدر", callback_data=f"confirm_delete_source_{source_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="manage_sources")]
    ]

    await query.edit_message_text(
        f"📚 **إدارة المصدر**\n\n"
        f"المصدر: **{source['name']}**\n"
        f"عدد عناوين المصدر: {titles_count}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def start_add_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ **إضافة مصدر**\n\nأرسل اسم المصدر الجديد:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_sources")]])
    )
    return STATE_SOURCE_ADD


async def receive_new_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sanitize_input(update.message.text)
    source_id = db.add_source(name)
    if source_id:
        await update.message.reply_text(
            f"✅ تم إضافة المصدر: **{name}**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]]),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "⚠️ لم يتم إضافة المصدر (قد يكون موجودًا بالفعل).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]])
        )
    return ConversationHandler.END


async def start_edit_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source_id = int(query.data.split("_")[-1])
    context.user_data["edit_source_id"] = source_id
    await query.edit_message_text(
        "✏️ **تعديل اسم المصدر**\n\nأرسل الاسم الجديد للمصدر:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_source_{source_id}")]])
    )
    return STATE_SOURCE_EDIT


async def receive_edit_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = sanitize_input(update.message.text)
    source_id = context.user_data.get("edit_source_id")
    if not source_id:
        await update.message.reply_text(
            "❌ لم يتم تحديد مصدر للتعديل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]])
        )
        return ConversationHandler.END


    # Check if new name exists (and implies a merge)
    sources = db.get_sources(search_query=new_name)
    target_exists = False
    for sid, sname in sources:
         if sname.strip() == new_name.strip() and sid != source_id:
             target_exists = True
             break
    
    if target_exists:
        if db.merge_sources(source_id, new_name):
             await update.message.reply_text(
                f"✅ تم دمج المصدر بنجاح مع: **{new_name}**",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]]),
                parse_mode='Markdown'
            )
        else:
             await update.message.reply_text(
                "⚠️ فشل دمج المصدر.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_source_{source_id}")]])
            )
        return ConversationHandler.END

    if db.update_source(source_id, new_name):
        await update.message.reply_text(
            f"✅ تم تحديث اسم المصدر إلى: **{new_name}**",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_source_{source_id}")]]) if not target_exists else InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]]),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "⚠️ لم يتم تحديث المصدر (قد يكون الاسم موجودًا بالفعل).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_source_{source_id}")]])
        )
    return ConversationHandler.END


async def confirm_delete_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source_id = int(query.data.split("_")[-1])
    source = db.get_source(source_id)
    if not source:
        await query.edit_message_text(
            "❌ المصدر غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_sources")]])
        )
        return

    titles_count = db.get_source_titles_count(source_id)
    if titles_count > 0:
        await query.edit_message_text(
            "⚠️ **لا يمكن حذف المصدر**\n\nهذا المصدر يحتوي على عناوين مرتبطة به.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_source_{source_id}")]]),
            parse_mode='Markdown'
        )
        return

    keyboard = [
        [InlineKeyboardButton("✅ نعم، احذف", callback_data=f"delete_source_{source_id}")],
        [InlineKeyboardButton("🔙 رجوع", callback_data=f"manage_source_{source_id}")]
    ]
    await query.edit_message_text(
        f"🗑️ **تأكيد الحذف**\n\nهل أنت متأكد من حذف المصدر: **{source['name']}**؟",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def delete_source_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    source_id = int(query.data.split("_")[-1])
    if db.delete_source(source_id):
        await query.edit_message_text(
            "✅ تم حذف المصدر بنجاح.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]])
        )
    else:
        await query.edit_message_text(
            "⚠️ تعذر حذف المصدر.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة المصادر", callback_data="manage_sources")]])
        )


# Source conv for add/edit
source_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_source, pattern='^add_source$'),
        CallbackQueryHandler(start_edit_source, pattern='^edit_source_')
    ],
    states={
        STATE_SOURCE_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_source)],
        STATE_SOURCE_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_source)]
    },
    fallbacks=[CallbackQueryHandler(manage_sources, pattern='^manage_sources')]
)


topic_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_topic_admin, pattern='^add_topic_cat_'),
        CallbackQueryHandler(start_edit_topic_admin, pattern='^edit_topic_'),
        CallbackQueryHandler(manage_topic_handler, pattern='^manage_topic_'),
        CallbackQueryHandler(confirm_delete_topic_handler, pattern='^confirm_delete_topic_'),
        CallbackQueryHandler(delete_topic_handler, pattern='^delete_topic_')
    ],
    states={
        STATE_TOPIC_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_topic)],
        STATE_TOPIC_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_topic_name)]
    },
    fallbacks=[CallbackQueryHandler(view_topics_handler, pattern='^view_topics_cat_')]
)


scholar_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_scholar_admin, pattern='^scholar_add_start$'),
        CallbackQueryHandler(start_add_scholar_bio, pattern='^scholar_bio_')
    ],
    states={
        STATE_SCHOLAR_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_scholar_admin)],
        STATE_SCHOLAR_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar_bio)],
        STATE_SCHOLAR_BIO_CONFIRM: [CallbackQueryHandler(confirm_scholar_bio, pattern='^scholar_bio_done$')],
        STATE_SCHOLAR_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar_website)]
    },
    fallbacks=[
        CallbackQueryHandler(manage_scholars_panel, pattern='^manage_scholars$'),
        CallbackQueryHandler(view_scholar_admin, pattern='^scholar_view_')
    ]
)

podcast_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(podcast_panel, pattern='^podcast_panel$'),
        CallbackQueryHandler(start_edit_podcast_broadcast, pattern='^podcast_edit_bc$'),
        CallbackQueryHandler(delete_podcast_broadcast, pattern='^podcast_del_bc$')
    ],
    states={
        STATE_PODCAST_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_podcast_content)],
        STATE_PODCAST_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_podcast_edit)]
    },
    fallbacks=[CallbackQueryHandler(admin_panel, pattern='^admin_panel$')]
)

settings_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_set_daily_time, pattern='^set_daily_time$'),
        CallbackQueryHandler(start_set_weekly_time, pattern='^set_weekly_time$'),
    ],
    states={
        STATE_SETTINGS_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_daily_time)],
        STATE_SETTINGS_WEEKLY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weekly_time)],
    },
    fallbacks=[
        CallbackQueryHandler(settings_panel, pattern='^admin_settings$'),
        CallbackQueryHandler(admin_panel, pattern='^admin_panel$')
    ]
)
