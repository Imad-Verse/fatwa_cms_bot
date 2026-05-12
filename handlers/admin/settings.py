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
from core.config import BotState
from core.utils import (
    sanitize_input, create_main_keyboard, 
    back_to_categories_keyboard, escape_markdown, notify_new_subscription
)
from handlers.general import cancel_operation, start_refresh, back_to_main
from handlers.admin.panel import admin_panel

logger = logging.getLogger(__name__)

# Singletons for Database Managers
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

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

    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    context.user_data.pop("settings_input_mode", None)
    daily_time = await bot_db.get_setting("daily_publish_time", "12:00") or "12:00"
    weekly_time = await bot_db.get_setting("weekly_report_time", "08:00") or "08:00"
    weekly_day_raw = await bot_db.get_setting("weekly_report_weekday", "4") or "4"
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
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END
    context.user_data["settings_input_mode"] = "daily_time"
    await query.edit_message_text(
        "⏰ **تغيير وقت النشر اليومي**\n\nأرسل الوقت بصيغة `HH:MM` (مثال: 08:00)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]]),
        parse_mode='Markdown'
    )
    return BotState.STATE_SETTINGS_DAILY_TIME

async def start_set_weekly_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END
    context.user_data["settings_input_mode"] = "weekly_time"
    await query.edit_message_text(
        "⏰ **تغيير وقت التقرير الأسبوعي**\n\nأرسل الوقت بصيغة `HH:MM` (مثال: 08:00)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="admin_settings")]]),
        parse_mode='Markdown'
    )
    return BotState.STATE_SETTINGS_WEEKLY_TIME

async def start_set_weekly_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    current_raw = await bot_db.get_setting("weekly_report_weekday", "4") or "4"
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
    if not await bot_db.is_admin(update.effective_user.id):
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

    await bot_db.set_setting("weekly_report_weekday", str(day_idx))
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
        return BotState.STATE_SETTINGS_DAILY_TIME

    hh, mm = parsed
    time_str = _format_time(hh, mm)
    await bot_db.set_setting("daily_publish_time", time_str)
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
        return BotState.STATE_SETTINGS_WEEKLY_TIME

    hh, mm = parsed
    time_str = _format_time(hh, mm)
    await bot_db.set_setting("weekly_report_time", time_str)
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

settings_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_set_daily_time, pattern='^set_daily_time$'),
        CallbackQueryHandler(start_set_weekly_time, pattern='^set_weekly_time$'),
    ],
    states={
        BotState.STATE_SETTINGS_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_daily_time)],
        BotState.STATE_SETTINGS_WEEKLY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weekly_time)],
    },
    fallbacks=[
        CallbackQueryHandler(settings_panel, pattern='^admin_settings$'),
        CallbackQueryHandler(admin_panel, pattern='^admin_panel$')
    ]
)
