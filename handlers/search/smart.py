import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from core.bot_db import BotDatabaseManager
from core.database import FatwaDatabaseManager
from core.config import BotState
from core.utils import callback_guard
from .keyboards import _build_smart_search_keyboard
from .pagination import display_search_results
from .logic import _fetch_smart_fatwas

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

def _get_smart_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    state = context.user_data.get('smart_search')
    if not isinstance(state, dict):
        state = {'title': False, 'text': False, 'scholars': []}
        context.user_data['smart_search'] = state
    state.setdefault('title', False)
    state.setdefault('text', False)
    state.setdefault('scholars', [])
    return state

def _reset_smart_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('smart_search', None)
    context.user_data.pop('smart_search_pending', None)

async def _render_smart_menu(query, context: ContextTypes.DEFAULT_TYPE):
    state = _get_smart_state(context)
    text = "🎛️ **البحث المتقدم**\n\nقم باختيار طريقة البحث بالضغط على الفلاتر التالية:"
    await query.edit_message_text(text, reply_markup=_build_smart_search_keyboard(state), parse_mode='Markdown')

@callback_guard(1.0)
async def start_smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _render_smart_menu(query, context)
    return STATE_SEARCH_SMART

async def smart_toggle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = _get_smart_state(context)
    state['title'] = not state['title']
    await _render_smart_menu(query, context)
    return BotState.STATE_SEARCH_SMART

async def smart_toggle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = _get_smart_state(context)
    state['text'] = not state['text']
    await _render_smart_menu(query, context)
    return BotState.STATE_SEARCH_SMART

async def smart_open_scholars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_smart_scholars_list(query, context)
    return BotState.STATE_SMART_SEARCH_SCHOLARS

async def smart_search_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = _get_smart_state(context)
    if not state['title'] and not state['text'] and not state['scholars']:
        await query.message.reply_text("⚠️ يرجى اختيار فلتر واحد على الأقل.")
        return BotState.STATE_SEARCH_SMART
    
    await query.edit_message_text("⌨️ يرجى إرسال كلمة البحث:")
    return BotState.STATE_SMART_SEARCH_QUERY

async def smart_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _reset_smart_state(context)
    from .keyboards import create_search_keyboard
    await query.edit_message_text("🔎 قائمة البحث:", reply_markup=create_search_keyboard())
    return ConversationHandler.END

async def show_smart_scholars_list(update_obj, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    scholars, total = await db.get_all_scholars(limit=10, offset=page*10)
    state = _get_smart_state(context)
    selected_scholars = set(state['scholars'])

    keyboard = []
    for s in scholars:
        label = f"✅ {s['name']}" if s['id'] in selected_scholars else s['name']
        keyboard.append([InlineKeyboardButton(label, callback_data=f"smart_sch_{s['id']}_{page}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"smart_sch_page_{page-1}"))
    if (page + 1) * 10 < total:
        nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"smart_sch_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom

    keyboard.append([InlineKeyboardButton("🔙 إنهاء الاختيار", callback_data="search_smart")])
    
    text = "👤 **اختر العلماء للبحث في فتاواهم:**"
    if update_obj.message:
        await update_obj.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update_obj.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def handle_smart_scholar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split('_')
    
    if data[2] == 'page':
        page = int(data[3])
        await show_smart_scholars_list(query, context, page)
        return BotState.STATE_SMART_SEARCH_SCHOLARS

    scholar_id = int(data[2])
    page = int(data[3])
    state = _get_smart_state(context)
    
    if scholar_id in state['scholars']:
        state['scholars'].remove(scholar_id)
    else:
        state['scholars'].append(scholar_id)
    
    await show_smart_scholars_list(query, context, page)
    return BotState.STATE_SMART_SEARCH_SCHOLARS

async def perform_smart_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    await _execute_smart_search(update, context, query_text, is_callback=False)
    return ConversationHandler.END

async def _execute_smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str | None, is_callback: bool):
    state = _get_smart_state(context)
    use_title = bool(state.get('title'))
    use_text = bool(state.get('text'))
    scholar_ids = list(state.get('scholars') or [])
    public_only = not await bot_db.is_admin(update.effective_user.id)
    
    context.user_data['current_search_state'] = {
        'type': 'smart',
        'params': {
            'query': query_text,
            'use_title': use_title,
            'use_text': use_text,
            'scholars': scholar_ids,
            'public': public_only
        }
    }
    
    results, total_count = await _fetch_smart_fatwas(query_text, use_title, use_text, scholar_ids, public_only, limit=5, offset=0)
    await display_search_results(update, context, results, 'نتائج البحث المتقدم', total_count, is_callback=is_callback, back_callback='search_smart')
