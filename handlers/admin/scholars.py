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
    """عرض قائمة العلماء في لوحة الإدارة مع دعم البحث"""
    query = update.callback_query
    if query: await query.answer()

    user_id = update.effective_user.id
    if not await bot_db.is_admin(user_id):
        if query: await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    # استخراج رقم الصفحة والبحث من callback_data أو user_data
    page = 0
    search_query = context.user_data.get('admin_scholar_search')

    if query and query.data:
        data = query.data
        if "scholars_list_" in data:
            parts = data.split("_")
            page = int(parts[-1])
        elif data == "clear_admin_schol_search":
            context.user_data.pop('admin_scholar_search', None)
            search_query = None
            page = 0

    ITEMS_PER_PAGE = 10
    offset = page * ITEMS_PER_PAGE

    scholars = await db.get_scholars_with_ids(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = await db.get_scholars_count(search_query=search_query)

    if not scholars and page == 0:
        msg = "📭 لا يوجد علماء مضافين حالياً." if not search_query else f"🔍 لا توجد نتائج للبحث عن: '{search_query}'"
        keyboard = []
        if search_query:
            keyboard.append([InlineKeyboardButton("❌ مسح البحث", callback_data="clear_admin_schol_search")])
        keyboard.append([InlineKeyboardButton("🔙 إدارة العلماء", callback_data="manage_scholars")])
        
        if query:
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = f"👤 **إدارة العلماء**\n"
    if search_query:
        text += f"🔍 نتائج البحث عن: `{search_query}`\n"
    text += f"صفحة {page + 1} | إجمالي: {total_count}\n\n"
    
    keyboard = []
    
    # قائمة العلماء
    for s in scholars:
        keyboard.append([InlineKeyboardButton(s['name'], callback_data=f"scholar_view_{s['id']}")])

    # أزرار التنقل
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"scholars_list_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"scholars_list_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    # أزرار البحث والتحكم
    keyboard.append([
        InlineKeyboardButton("🔍 بحث باسم العالم", callback_data="admin_schol_search_start"),
        InlineKeyboardButton("➕ إضافة عالم", callback_data="scholar_add_start")
    ])
    
    if search_query:
        keyboard.append([InlineKeyboardButton("❌ مسح البحث", callback_data="clear_admin_schol_search")])

    keyboard.append([InlineKeyboardButton("🏠 لوحة الإدارة", callback_data="admin_panel")])

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def start_search_schol_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البحث عن عالم"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🔍 **البحث عن عالم**\n\nأرسل اسم العالم (أو جزء منه):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="scholars_list_0")]]),
        parse_mode='Markdown'
    )
    return BotState.STATE_ADMIN_SEARCH_SCHOLAR


async def handle_scholar_search_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام كلمة البحث"""
    query_text = update.message.text.strip()
    if not query_text:
        await update.message.reply_text("⚠️ يرجى إرسال نص للبحث.")
        return BotState.STATE_ADMIN_SEARCH_SCHOLAR
    
    context.user_data['admin_scholar_search'] = query_text
    await show_scholars_admin(update, context)
    return ConversationHandler.END


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
            f"✅ تم إضافة العالم بنجاح: *{safe_name}*",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ إضافة سيرة ذاتية", callback_data=f"scholar_bio_{scholar_id}")],
                [InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]
            ]),
            parse_mode='Markdown'
        )
    else:
        # هنا scholar_id هو None، مما يعني أنه موجود مسبقاً بناءً على التعديل الجديد في add_scholar
        await update.message.reply_text(
            "⚠️ هذا العالم موجود مسبقاً في النظام.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة العلماء", callback_data="scholars_list_0")]])
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
        CallbackQueryHandler(start_add_scholar_bio, pattern='^scholar_bio_'),
        CallbackQueryHandler(start_search_schol_admin, pattern='^admin_schol_search_start$')
    ],
    states={
        BotState.STATE_SCHOLAR_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_scholar_admin)],
        BotState.STATE_SCHOLAR_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar_bio)],
        BotState.STATE_SCHOLAR_BIO_CONFIRM: [CallbackQueryHandler(confirm_scholar_bio, pattern='^scholar_bio_done$')],
        BotState.STATE_SCHOLAR_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar_website)],
        BotState.STATE_ADMIN_SEARCH_SCHOLAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_scholar_search_admin)]
    },
    fallbacks=[
        CallbackQueryHandler(manage_scholars_panel, pattern='^manage_scholars$'),
        CallbackQueryHandler(show_scholars_admin, pattern='^scholars_list'),
        CallbackQueryHandler(view_scholar_admin, pattern='^scholar_view_')
    ]
)


# ==============================================================================
# 🎙️ القسم 5: البودكاست والإذاعة العامة (Podcast & Broadcast)
# ==============================================================================

