import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.utils import safe_reply_text, safe_edit_message_text
from .utils import _ensure_admin
from .autopublish import (
    auto_publish_panel, _get_scheduled_fatwa, AWAITING_SCHEDULED_FATWA_INPUT_KEY
)
from .jobs import daily_fatwa_job

logger = logging.getLogger(__name__)

async def force_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نشر فتوى فوراً (يدوياً)"""
    query = update.callback_query; await query.answer("جاري النشر...")
    if not await _ensure_admin(update, query): return
    # جعلنا respect_scheduled=True لنشر الفتوى المجدولة فوراً إذا وجدت
    await daily_fatwa_job(context, force=True, respect_scheduled=True, trigger_admin_id=update.effective_user.id)
    await auto_publish_panel(update, context)

