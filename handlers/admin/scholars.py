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

async def manage_scholars_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة خيارات إدارة العلماء"""
    query = update.callback_query
    await query.answer()

    if not await bot_db.is_admin(update.effective_user.id):
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
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return


    page = 0
    data = query.data
    if "scholars_list_" in data:
        page = int(data.split("_")[-1])

    ITEMS_PER_PAGE = 10
    offset = page * ITEMS_PER_PAGE

    scholars = await db.get_scholars_with_ids(limit=ITEMS_PER_PAGE, offset=offset)
    total_count = await db.get_scholars_count()

    if not scholars and page == 0:
        await query.edit_message_text(
            "📭 لا يوجد علماء مضافين حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")]])
        )
        return

    text = f"👤 **إدارة العلماء** (صفحة {page + 1})\nإجمالي العلماء: {total_count}\n\n"
    keyboard = []

    # Navigation Buttons (Top)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"scholars_list_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"scholars_list_{page+1}"))
    
    if nav_buttons:
        keyboard.append(nav_buttons)

    for s in scholars:
        keyboard.append([InlineKeyboardButton(s['name'], callback_data=f"scholar_view_{s['id']}")])

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def view_scholar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض بيانات عالم محدد"""
    query = update.callback_query
    await query.answer()
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return


    scholar_id = int(query.data.split("_")[-1])
    scholar = await db.get_scholar_by_id(scholar_id)
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
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END


    await query.edit_message_text(
        "👤 **إضافة عالم جديد**\n\nالرجاء إرسال اسم العالم الكامل:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")]]),
        parse_mode='Markdown'
    )
    return BotState.STATE_SCHOLAR_ADD


async def receive_new_scholar_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sanitize_input(update.message.text, max_length=200)
    if not name:
        await update.message.reply_text("⚠️ عذراً، يجب إرسال اسم العالم بشكل نصي.")
        return BotState.STATE_SCHOLAR_ADD

    scholar_id = await db.add_scholar(name)
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
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return ConversationHandler.END


    scholar_id = int(query.data.split("_")[-1])
    scholar = await db.get_scholar_by_id(scholar_id)
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
    return BotState.STATE_SCHOLAR_BIO


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
    return BotState.STATE_SCHOLAR_BIO_CONFIRM


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
    return BotState.STATE_SCHOLAR_WEBSITE


async def receive_scholar_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scholar_id = context.user_data.get('scholar_bio_id')
    bio = context.user_data.get('scholar_bio_text', '')
    website = sanitize_input(update.message.text, max_length=500)

    if not scholar_id:
        await update.message.reply_text("⚠️ حدث خطأ في البيانات.")
        return ConversationHandler.END

    await db.update_scholar_bio_website(scholar_id, bio, website)
    context.user_data.pop('scholar_bio_id', None)
    context.user_data.pop('scholar_bio_text', None)

    await update.message.reply_text(
        "✅ تم تحديث بيانات العالم بنجاح.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]])
    )
    return ConversationHandler.END

scholar_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_scholar_admin, pattern='^scholar_add_start$'),
        CallbackQueryHandler(start_add_scholar_bio, pattern='^scholar_bio_')
    ],
    states={
        BotState.STATE_SCHOLAR_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_scholar_admin)],
        BotState.STATE_SCHOLAR_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar_bio)],
        BotState.STATE_SCHOLAR_BIO_CONFIRM: [CallbackQueryHandler(confirm_scholar_bio, pattern='^scholar_bio_done$')],
        BotState.STATE_SCHOLAR_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar_website)]
    },
    fallbacks=[
        CallbackQueryHandler(manage_scholars_panel, pattern='^manage_scholars$'),
        CallbackQueryHandler(view_scholar_admin, pattern='^scholar_view_')
    ]
)


# ==============================================================================
# 🎙️ القسم 5: البودكاست والإذاعة العامة (Podcast & Broadcast)
# ==============================================================================

