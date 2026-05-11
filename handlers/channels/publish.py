import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .utils import _ensure_admin, _safe_edit_message_text
from .autopublish import (
    auto_publish_panel, _get_scheduled_fatwa, AWAITING_SCHEDULED_FATWA_INPUT_KEY
)
from .jobs import daily_fatwa_job

logger = logging.getLogger(__name__)

async def force_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نشر فتوى فوراً"""
    query = update.callback_query; await query.answer("جاري النشر...")
    if not await _ensure_admin(update, query): return
    await daily_fatwa_job(context, force=True, respect_scheduled=False)
    await auto_publish_panel(update, context)

async def start_schedule_fatwa_once(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب رقم فتوى ليتم نشرها مرة واحدة."""
    query = update.callback_query; await query.answer()
    if not await _ensure_admin(update, query): return
    context.user_data[AWAITING_SCHEDULED_FATWA_INPUT_KEY] = True
    num, _ = await _get_scheduled_fatwa(); prompt = f"🗓️ **جدولة فتوى**\n\nأرسل رقم الفتوى للنشر القادم."
    if num: prompt += f"\n\nالجدولة الحالية: `{num}`\nأرسل رقمًا جديدًا للاستبدال."
    await _safe_edit_message_text(query, prompt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]]), parse_mode='Markdown')
