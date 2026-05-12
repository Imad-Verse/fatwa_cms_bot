import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import BotState
from core.utils import (
    callback_guard, sanitize_input,
    safe_reply_text, safe_edit_message_text
)
from .pagination import display_search_results
from .logic import _fetch_contextual_text_fatwas

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

@callback_guard(1.5)
async def start_browse_fatwas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from .keyboards import create_browse_keyboard
    await safe_edit_message_text(update, "📂 تصفح الفتاوى حسب:", reply_markup=create_browse_keyboard())
    return BotState.STATE_SEARCH

async def search_scholar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_scholars_list(query, context)
    return BotState.STATE_SEARCH

async def show_scholars_list(update_obj, context, page=0):
    scholars, total = await db.get_all_scholars(limit=10, offset=page*10)
    keyboard = []
    for s in scholars:
        keyboard.append([InlineKeyboardButton(s['name'], callback_data=f"sel_sch_{s['id']}")])
    
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sch_page_{page-1}"))
    if (page+1)*10 < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sch_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_fatwas")])
    text = "👤 **اختر العالم:**"
    await safe_reply_text(update_obj, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_scholar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    if data[1] == 'page':
        await show_scholars_list(query, context, int(data[2]))
        return BotState.STATE_SEARCH
    
    scholar_id = int(data[2])
    scholar = await db.get_scholar(scholar_id)
    if not scholar: return BotState.STATE_SEARCH
    
    public_only = not await bot_db.is_admin(update.effective_user.id)
    context.user_data['current_search_state'] = {
        'type': 'scholar',
        'params': {'scholar_id': scholar_id, 'scholar_name': scholar['name'], 'public': public_only}
    }
    results, total = await db.get_all_fatwas(scholar_id=scholar_id, status='published' if public_only else None, limit=5, offset=0)
    await display_search_results(update, context, results, f"فتاوى الشيخ: {scholar['name']}", total, is_callback=True, back_callback="search_scholar")
    return BotState.STATE_SEARCH

async def search_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_categories_list(query, context)
    return BotState.STATE_SEARCH

async def show_categories_list(update_obj, context, page=0, search_query=None):
    categories, total = await db.get_all_categories(limit=10, offset=page*10)
    keyboard = []
    for c in categories:
        keyboard.append([InlineKeyboardButton(c['name'], callback_data=f"sel_cat_{c['id']}")])
    
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cat_page_{page-1}"))
    if (page+1)*10 < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"cat_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_fatwas")])
    text = "🗂️ **اختر التصنيف:**"
    await safe_reply_text(update_obj, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_category_search_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    if data[1] == 'page':
        await show_categories_list(query, context, int(data[2]))
        return BotState.STATE_SEARCH
    
    cat_id = int(data[2])
    await show_topics_list(query, context, cat_id)
    return BotState.STATE_SEARCH

async def show_topics_list(update_obj, context, cat_id, page=0, search_query=None):
    topics, total = await db.get_topics_by_category(cat_id, limit=10, offset=page*10)
    cat = await db.get_category(cat_id)
    keyboard = [[InlineKeyboardButton("🔍 عرض الكل في هذا التصنيف", callback_data=f"cat_all_{cat_id}")]]
    for t in topics:
        keyboard.append([InlineKeyboardButton(t['name'], callback_data=f"sel_top_{cat_id}_{t['id']}")])
    
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"top_page_{cat_id}_{page-1}"))
    if (page+1)*10 < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"top_page_{cat_id}_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتصنيفات", callback_data="search_category")])
    text = f"📚 **مواضيع تصنيف: {cat['name'] if cat else 'غير معروف'}**"
    await safe_edit_message_text(update_obj, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_topic_search_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    if data[1] == 'page':
        await show_topics_list(query, context, int(data[2]), int(data[3]))
        return BotState.STATE_SEARCH
    
    cat_id = int(data[2])
    topic_id = None if data[1] == 'all' else int(data[3])
    await fetch_and_display_cat_fatwas(query, context, cat_id, topic_id)
    return BotState.STATE_SEARCH

async def fetch_and_display_cat_fatwas(update_obj, context, cat_id, topic_id=None):
    cat = await db.get_category(cat_id)
    topic = await db.get_topic(topic_id) if topic_id else None
    public_only = not await bot_db.is_admin(update_obj.from_user.id)
    
    context.user_data['current_search_state'] = {
        'type': 'category',
        'params': {'cat_id': cat_id, 'cat_name': cat['name'] if cat else 'غير معروف', 'topic_id': topic_id, 'topic_name': topic['name'] if topic else None, 'public': public_only}
    }
    results, total = await db.search_fatwas(category_id=cat_id, topic_id=topic_id, public_only=public_only, limit=5, offset=0)
    title = f"تصنيف: {cat['name'] if cat else 'غير معروف'}" + (f" - {topic['name']}" if topic else "")
    back_cb = f"view_topics_{cat_id}" if topic_id else "search_category"
    await display_search_results(update_obj, context, results, title, total, is_callback=True, back_callback=back_cb)

async def search_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_sources_list(query, context)
    return BotState.STATE_SEARCH

async def show_sources_list(update_obj, context, page=0, search_query=None):
    sources, total = await db.get_all_sources(limit=10, offset=page*10)
    keyboard = []
    for s in sources:
        keyboard.append([InlineKeyboardButton(s['title'], callback_data=f"sel_src_{s['id']}")])
    
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"src_page_{page-1}"))
    if (page+1)*10 < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"src_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_fatwas")])
    text = "📚 **اختر المصدر:**"
    await safe_reply_text(update_obj, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    if data[1] == 'page':
        await show_sources_list(query, context, int(data[2]))
        return BotState.STATE_SEARCH
    
    source_id = int(data[2])
    source = await db.get_source(source_id)
    public_only = not await bot_db.is_admin(update.effective_user.id)
    
    context.user_data['current_search_state'] = {
        'type': 'source',
        'params': {'source_id': source_id, 'source_name': source['title'] if source else 'غير معروف', 'public': public_only}
    }
    results, total = await db.get_all_fatwas(source_id=source_id, status='published' if public_only else None, limit=5, offset=0)
    await display_search_results(update, context, results, f"مصدر: {source['title'] if source else 'غير معروف'}", total, is_callback=True, back_callback="search_source")
    return BotState.STATE_SEARCH

async def handle_search_cat_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This might be for searching within categories if implemented
    return BotState.STATE_SEARCH

async def handle_search_topic_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return BotState.STATE_SEARCH

async def handle_search_source_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return BotState.STATE_SEARCH

async def show_scholar_fatwas_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Proxy for direct links or similar
    return BotState.STATE_SEARCH
