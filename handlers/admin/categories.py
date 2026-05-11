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
    categories = await db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type)
    total_count = await db.get_categories_count(search_query=search_query, category_type=cat_type)

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

    # Navigation Row (Top)
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"manage_categories_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"manage_categories_page_{page+1}"))
    
    if nav_row:
        keyboard.append(nav_row)

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

    # Navigation Row (Bottom)
    if nav_row:
        keyboard.append(nav_row)

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

    if await db.add_category(name, category_type=cat_type):
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

    if await db.update_category(cat_id, new_name):
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
        BotState.STATE_CATEGORY_ADD: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_category)
        ],
        BotState.STATE_ADMIN_SEARCH_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category_search_term)],
        BotState.STATE_CATEGORY_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_category_name)]
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

    if await db.add_topic(name, cat_id):
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

        topics = await db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset)
        total_count = await db.get_topics_count(cat_id)

        # نحتاج اسم التصنيف للعرض
        all_cats = dict(await db.get_categories())
        cat_name = all_cats.get(cat_id, "غير معروف")

        keyboard = []

        # أزرار الإضافة والتعديل (متقابلة)
        action_row = [
            InlineKeyboardButton("➕ موضوع جديد", callback_data=f"add_topic_cat_{cat_id}"),
            InlineKeyboardButton("✏️ تعديل التصنيف", callback_data=f"edit_category_{cat_id}")
        ]
        keyboard.append(action_row)
        keyboard.append([InlineKeyboardButton("🗑️ حذف التصنيف", callback_data=f"confirm_delete_category_{cat_id}")])

        # Navigation Buttons (Shared for Top and Bottom)
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"view_topics_cat_{cat_id}_page_{page-1}"))
        if offset + ITEMS_PER_PAGE < total_count:
            nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"view_topics_cat_{cat_id}_page_{page+1}"))
            
        if nav_row:
            keyboard.append(nav_row)

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

        # Bottom Navigation
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
    topic = await db.get_topic(topic_id)

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

    topic = await db.get_topic(topic_id)
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

    topic = await db.get_topic(topic_id)
    cat_id = topic['category_id']

    if await db.update_topic(topic_id, new_name):
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
    topic = await db.get_topic(topic_id)

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
    topic = await db.get_topic(topic_id) # جلب البيانات قبل الحذف لمعرفة التصنيف
    cat_id = topic['category_id']

    if await db.delete_topic(topic_id):
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
    category = await db.get_category(cat_id)
    if not category:
        await query.edit_message_text(
            "❌ التصنيف غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_categories")]])
        )
        return

    topics_count = await db.get_topics_count(cat_id)
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
    category = await db.get_category(cat_id)
    if not category:
        await query.edit_message_text(
            "❌ التصنيف غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_categories")]])
        )
        return

    if await db.delete_category(cat_id):
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



topic_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_topic_admin, pattern='^add_topic_cat_'),
        CallbackQueryHandler(start_edit_topic_admin, pattern='^edit_topic_'),
        CallbackQueryHandler(manage_topic_handler, pattern='^manage_topic_'),
        CallbackQueryHandler(confirm_delete_topic_handler, pattern='^confirm_delete_topic_'),
        CallbackQueryHandler(delete_topic_handler, pattern='^delete_topic_')
    ],
    states={
        BotState.STATE_TOPIC_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_topic)],
        BotState.STATE_TOPIC_EDIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_topic_name)]
    },
    fallbacks=[CallbackQueryHandler(view_topics_handler, pattern='^view_topics_cat_')]
)

