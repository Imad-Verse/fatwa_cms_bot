import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.utils import (
    format_fatwa_card, callback_guard, 
    safe_reply_text, safe_edit_message_text
)
from core.keyboards import create_pagination_keyboard

logger = logging.getLogger(__name__)

async def display_search_results(update, context, results, title, total_count, is_callback=False, page=0, back_callback="search_fatwas", back_label=None):
    """عرض نتائج البحث بتنسيق عصري واحترافي."""
    if not results:
        text = f"⚠️ **{title}**\n\nلم يتم العثور على أي نتائج تطابق بحثك."
        keyboard = [[InlineKeyboardButton(back_label or "🔙 رجوع", callback_data=back_callback)]]
        await safe_reply_text(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return

    pages_total = (total_count + 4) // 5
    header = (
        f"🔍 **{title}**\n"
        f"───\n"
        f"📊 النتائج: `{total_count}` | الصفحة: `{page + 1}/{pages_total}`\n\n"
    )

    cards = []
    for i, fatwa in enumerate(results):
        # استخدام format_fatwa_card مع markdown
        cards.append(format_fatwa_card(fatwa, use_markdown=True))

    text = header + "\n\n".join(cards)

    # Pagination keyboard
    keyboard = []
    
    # Navigation buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"res_page_{page-1}"))
    if (page + 1) * 5 < total_count:
        nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"res_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top

    # Fatwa quick view buttons
    for fatwa in results:
        keyboard.append([InlineKeyboardButton(f"📖 عرض #{fatwa['fatwa_number']}", callback_data=f"view_{fatwa['id']}")])

    if nav_row:
        keyboard.append(nav_row)    # Bottom

    # Back button
    keyboard.append([InlineKeyboardButton(back_label or "🔙 رجوع", callback_data=back_callback)])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if is_callback:
        await safe_edit_message_text(update, text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await safe_reply_text(update, text, reply_markup=reply_markup, parse_mode='Markdown')

@callback_guard(1.5)
async def handle_search_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أزرار التنقل بين صفحات النتائج."""
    query = update.callback_query
    await query.answer()

    try:
        page = int(query.data.split('_')[-1])
    except (ValueError, IndexError):
        return

    search_state = context.user_data.get('current_search_state')
    if not search_state:
        await safe_reply_text(update, "❌ انتهت جلسة البحث، يرجى البدء من جديد.")
        return

    stype = search_state.get('type')
    params = search_state.get('params', {})
    limit = 5
    offset = page * limit

    # Re-import logic to avoid circular dependency
    from .logic import _fetch_smart_fatwas, _fetch_popular_fatwas, _fetch_ai_text_fatwas, _fetch_contextual_text_fatwas

    results = []
    total_count = 0
    title = "نتائج البحث"
    back_callback = "search_fatwas"
    back_label = "🔙 رجوع"

    if stype == 'title':
        results, total_count = await _fetch_contextual_text_fatwas(base_terms=[], user_query=params.get('query'), public_only=params['public'], limit=limit, offset=offset)
        title = f"نتائج البحث عن: {params.get('query')}"
    elif stype == 'all':
        results, total_count = await _fetch_contextual_text_fatwas(base_terms=[], user_query=params.get('query'), public_only=params['public'], limit=limit, offset=offset)
        title = f"نتائج البحث الشامل عن: {params.get('query')}"
    elif stype == 'scholar':
        from core.database import FatwaDatabaseManager
        db = FatwaDatabaseManager()
        results, total_count = await db.get_all_fatwas(scholar_id=params.get('scholar_id'), status='published' if params['public'] else None, limit=limit, offset=offset)
        title = f"فتاوى الشيخ: {params.get('scholar_name')}"
        back_callback = f"scholar_view_{params.get('scholar_id')}"
    elif stype == 'category':
        from core.database import FatwaDatabaseManager
        db = FatwaDatabaseManager()
        results, total_count = await db.search_fatwas(category_id=params.get('cat_id'), topic_id=params.get('topic_id'), public_only=params['public'], limit=limit, offset=offset)
        title = f"فتاوى تصنيف: {params.get('cat_name')}"
        back_callback = f"sel_cat_{params.get('cat_id')}" if params.get('topic_id') else "search_category"
    elif stype == 'source':
        from core.database import FatwaDatabaseManager
        db = FatwaDatabaseManager()
        # Note: source filtering might need specific logic if not in db.search_fatwas
        results, total_count = await db.get_all_fatwas(source_id=params.get('source_id'), status='published' if params['public'] else None, limit=limit, offset=offset)
        title = f"فتاوى من مصدر: {params.get('source_name')}"
        back_callback = f"manage_source_{params.get('source_id')}"
    elif stype == 'smart':
        results, total_count = await _fetch_smart_fatwas(params.get('query'), params.get('use_title'), params.get('use_text'), params.get('scholars', []), params.get('public', True), limit, offset)
        title = "نتائج البحث المتقدم"
        back_callback = "search_smart"
    elif stype == 'latest':
        from core.database import FatwaDatabaseManager
        db = FatwaDatabaseManager()
        results, total_count = await db.get_all_fatwas(status='published' if params['public'] else None, limit=limit, offset=offset)
        title = "أحدث الفتاوى"
        back_callback = "browse_fatwas"
    elif stype == 'popular':
        results, total_count = await _fetch_popular_fatwas(params['public'], limit=limit, offset=offset, max_total=params.get('cap'))
        title = "الفتاوى الأكثر مشاهدة"
        back_callback = "browse_fatwas"
    elif stype == 'ai':
        results, total_count = await _fetch_ai_text_fatwas(params.get('terms'), params.get('user_query'), params.get('public', True), limit, offset, max_total=params.get('cap'))
        title = "نتائج البحث الذكي"
        back_callback = "search_fatwas"

    await display_search_results(update, context, results, title, total_count, is_callback=True, page=page, back_callback=back_callback, back_label=back_label)
