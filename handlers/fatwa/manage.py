import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.utils import (
    format_fatwa_content,
    back_to_main_keyboard as kb_back_main
)
from .utils import _DELIVERY_LOG_KEY

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# ==================== عمليات النشر والحذف ====================

async def delete_fatwa_from_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري الحذف من الكل...")
    if not await bot_db.is_admin(update.effective_user.id): return
    fatwa_id = int(query.data.split('_')[-1])
    app = getattr(context, "application", None)
    if not app: return
    store = app.bot_data.get(_DELIVERY_LOG_KEY, {})
    fatwa_store = store.get(str(fatwa_id), {})
    if not fatwa_store:
        await query.message.reply_text("ℹ️ لا توجد رسائل محفوظة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]))
        return
    deleted, failed = 0, 0
    for chat_id_raw, msg_ids in fatwa_store.items():
        for msg_id in msg_ids:
            try: await context.bot.delete_message(chat_id=int(chat_id_raw), message_id=int(msg_id)); deleted += 1
            except Exception: failed += 1
    store.pop(str(fatwa_id), None)
    await query.message.reply_text(f"✅ تم تنفيذ الحذف.\n🗑️ حُذفت: {deleted}\n⚠️ فشلت: {failed}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]))

async def publish_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await bot_db.is_admin(update.effective_user.id): return
    fatwa_id = int(query.data.split('_')[1])
    await db.update_fatwa(fatwa_id, {'status': 'published'})
    await query.answer("✅ تم النشر!")
    fatwa = await db.get_fatwa(fatwa_id)
    text = format_fatwa_content(fatwa)
    keyboard = [[InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"), InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")], [InlineKeyboardButton("📢 إرسال الفتوى", callback_data=f"broadcast_{fatwa_id}")], [InlineKeyboardButton("📋 نسخ النص", callback_data=f"copy_full_{fatwa_id}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_fatwa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; fatwa_id = int(query.data.split('_')[2])
    await query.edit_message_text("⚠️ **هل أنت متأكد؟**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ نعم، احذف", callback_data=f"delete_final_{fatwa_id}")], [InlineKeyboardButton("❌ إلغاء", callback_data=f"view_{fatwa_id}")]]), parse_mode='Markdown')

async def delete_fatwa_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; fatwa_id = int(query.data.split('_')[2])
    await db.delete_fatwa(fatwa_id); await bot_db.remove_favorites_for_fatwa(fatwa_id)
    await query.answer("🗑️ تم الحذف"); await query.edit_message_text("✅ تم حذف الفتوى.", reply_markup=kb_back_main())
