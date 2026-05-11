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

logger = logging.getLogger(__name__)

# Singletons for Database Managers
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

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

    if not await bot_db.is_admin(update.effective_user.id):
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
    return BotState.STATE_PODCAST_CONTENT


async def receive_podcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام محتوى البودكاست وبدء الإذاعة لجميع المشتركين"""
    if not await bot_db.is_admin(update.effective_user.id):
        await update.message.reply_text("❌ غير مصرح لك.")
        return ConversationHandler.END

    message = update.message
    if not message:
        return ConversationHandler.END

    users = await bot_db.get_active_user_ids()
    total_targets = len(users)

    if total_targets == 0:
        await update.message.reply_text("⚠️ لا يوجد مشتركون حالياً.")
        return ConversationHandler.END

    broadcast_id = f"{update.effective_user.id}_{int(datetime.now().timestamp())}"
    store = context.bot_data.setdefault('podcast_broadcasts', {})
    store[broadcast_id] = {'cancel': False}

    cancel_btn = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 إلغاء العملية", callback_data=f"podcast_cancel_{broadcast_id}")]])
    status_msg = await update.message.reply_text("🔄 جاري بدء عملية الإرسال...", reply_markup=cancel_btn)

    report = {'success': 0, 'fail': 0}
    canceled = False
    podcast_markup = _podcast_inline_keyboard()
    broadcast_text = _build_podcast_template(message.text or message.caption or "")
    sent_messages = []

    for idx, user_id in enumerate(users):
        if store.get(broadcast_id, {}).get('cancel'):
            canceled = True
            break
            
        try:
            msg = await _send_podcast_to_user(context, user_id, message, broadcast_text, podcast_markup)
            if msg:
                msg_id = getattr(msg, 'message_id', None)
                if msg_id:
                    sent_messages.append({"chat_id": user_id, "message_id": msg_id})
            report['success'] += 1
        except Exception as e:
            report['fail'] += 1
            err = str(e).lower()
            if 'forbidden' in err or 'bot was blocked' in err or 'user is deactivated' in err:
                await bot_db.set_user_blocked(user_id, True)
        
        # تأخير بسيط لتجنب الـ Flood Limits
        await asyncio.sleep(0.05)
        
        # تحديث رسالة التقدم كل 50 مستخدم
        if (idx + 1) % 50 == 0:
            try:
                await status_msg.edit_text(
                    f"🔄 جاري الإرسال... ({idx + 1}/{total_targets})\n✅ نجاح: {report['success']}\n❌ فشل: {report['fail']}",
                    reply_markup=cancel_btn
                )
            except Exception:
                pass

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

    await status_msg.edit_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
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
    return BotState.STATE_PODCAST_EDIT

async def receive_podcast_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام النص الجديد وتعديل الرسائل"""
    message = update.message
    if not message.text:
        await message.reply_text("⚠️ يرجى إرسال نص فقط للتعديل.")
        return BotState.STATE_PODCAST_EDIT
        
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

from handlers.admin.panel import admin_panel

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

podcast_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(podcast_panel, pattern='^podcast_panel$'),
        CallbackQueryHandler(start_edit_podcast_broadcast, pattern='^podcast_edit_bc$'),
        CallbackQueryHandler(delete_podcast_broadcast, pattern='^podcast_del_bc$')
    ],
    states={
        BotState.STATE_PODCAST_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_podcast_content)],
        BotState.STATE_PODCAST_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_podcast_edit)]
    },
    fallbacks=[CallbackQueryHandler(admin_panel, pattern='^admin_panel$')]
)


# ==============================================================================
# 👥 القسم 6: إدارة المشتركين (Subscribers Management)
# ==============================================================================

