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

async def manage_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المصادر مع Pagination"""
    query = update.callback_query
    if query: await query.answer()

    # التحقق من الصلاحيات
    user_id = update.effective_user.id
    if not await bot_db.is_admin(user_id):
        if query: await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    page = 0
    data = query.data if query else ""
    if "manage_sources_page_" in data:
        try:
            page = int(data.split("manage_sources_page_")[-1])
        except ValueError:
            page = 0

    ITEMS_PER_PAGE = 10
    offset = page * ITEMS_PER_PAGE

    try:
        sources = await db.get_sources(limit=ITEMS_PER_PAGE, offset=offset)
        total_count = await db.get_sources_count()
    except Exception as e:
        logger.error(f"Error fetching sources: {e}")
        error_msg = "❌ حدث خطأ أثناء تحميل المصادر."
        if query: await query.edit_message_text(error_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]))
        else: await update.message.reply_text(error_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]]))
        return

    keyboard = []
    keyboard.append([InlineKeyboardButton("➕ إضافة مصدر", callback_data="add_source")])

    if sources:
        row = []
        for s_id, s_name in sources:
             name = s_name
             row.append(InlineKeyboardButton(f"📚 {name}", callback_data=f"manage_source_{s_id}"))
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

    text = f"📚 **إدارة المصادر** (صفحة {page + 1})\nإجمالي المصادر: {total_count}\n\nاختر مصدرًا لإدارته:"

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def manage_source(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
    source = await db.get_source(source_id)
    if not source:
        await query.edit_message_text(
            "❌ المصدر غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_sources")]])
        )
        return

    titles_count = await db.get_source_titles_count(source_id)

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
    return BotState.STATE_SOURCE_ADD


async def receive_new_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = sanitize_input(update.message.text)
    source_id = await db.add_source(name)
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
    return BotState.STATE_SOURCE_EDIT


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
    sources = await db.get_sources(search_query=new_name)
    target_exists = False
    for sid, sname in sources:
         if sname.strip() == new_name.strip() and sid != source_id:
             target_exists = True
             break
    
    if target_exists:
        if await db.merge_sources(source_id, new_name):
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

    if await db.update_source(source_id, new_name):
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
    source = await db.get_source(source_id)
    if not source:
        await query.edit_message_text(
            "❌ المصدر غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_sources")]])
        )
        return

    titles_count = await db.get_source_titles_count(source_id)
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
    if await db.delete_source(source_id):
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
        BotState.STATE_SOURCE_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_source)],
        BotState.STATE_SOURCE_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_source)]
    },
    fallbacks=[CallbackQueryHandler(manage_sources, pattern='^manage_sources')]
)


