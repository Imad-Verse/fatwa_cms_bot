import logging
from telegram import Update
from telegram.ext import (
    ContextTypes, 
    ConversationHandler, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters
)
from core.config import BotState
from core.bot_db import BotDatabaseManager
from core.database import FatwaDatabaseManager

# Import from sub-modules
from .keyboards import create_search_keyboard, create_browse_keyboard
from .logic import (
    _fetch_popular_fatwas, 
    _fetch_ai_text_fatwas, 
    _fetch_contextual_text_fatwas,
    _request_ai_query_terms,
    LATEST_LIMIT,
    POPULAR_LIMIT
)
from .pagination import display_search_results, handle_search_pagination
from .smart import (
    start_smart_search,
    smart_toggle_title,
    smart_toggle_text,
    smart_open_scholars,
    smart_search_now,
    smart_cancel,
    handle_smart_scholar_selection,
    perform_smart_search_query
)
from .browse import (
    start_browse_fatwas,
    search_scholar,
    handle_scholar_selection,
    search_category,
    handle_category_search_selection,
    handle_topic_search_selection,
    search_source,
    handle_source_selection,
    handle_search_cat_query,
    handle_search_topic_query,
    handle_search_source_query,
    show_scholar_fatwas_by_id
)

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# --- Main Entry Points ---

async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("🔎 قائمة البحث:", reply_markup=create_search_keyboard())
    else:
        await update.message.reply_text("🔎 قائمة البحث:", reply_markup=create_search_keyboard())
    return BotState.STATE_SEARCH

async def search_ai_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🤖 **البحث بالذكاء الاصطناعي**\n\nأرسل سؤالك أو الحالة التي تبحث عنها، وسأقوم باستخراج المصطلحات الفقهية المناسبة والبحث عنها دلالياً:")
    return BotState.STATE_SEARCH_AI

async def perform_search_ai_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_query = update.message.text
    status_msg = await update.message.reply_text("⏳ جاري تحليل السؤال دلالياً...")
    
    from .logic import _generate_ai_answer
    
    terms, error = await _request_ai_query_terms(user_query)
    if error:
        await status_msg.edit_text(f"⚠️ حدث خطأ أثناء تحليل السؤال ({error}). جاري البحث المباشر...")
        terms = []

    public_only = not await bot_db.is_admin(update.effective_user.id)
    context.user_data['current_search_state'] = {
        'type': 'ai',
        'params': {
            'terms': terms,
            'user_query': user_query,
            'public': public_only,
            'cap': 10
        }
    }
    
    results, total = await _fetch_ai_text_fatwas(terms, user_query, public_only, limit=5, offset=0, max_total=10)
    
    if not results:
        await status_msg.edit_text("❌ عذراً، لم أجد أي فتاوى متعلقة بهذا السؤال في قاعدة البيانات.")
        return ConversationHandler.END

    await status_msg.edit_text("🧪 جاري صياغة الإجابة بناءً على الفتاوى المستخرجة...")
    ai_answer = await _generate_ai_answer(user_query, results)
    
    await status_msg.delete()
    
    if ai_answer:
        # تأكد من أن النص لا يتجاوز حد تليجرام
        header = "🤖 **الإجابة المستخلصة بالذكاء الاصطناعي:**\n\n"
        footer = "\n\n📚 **المصادر المعتمدة من قاعدة البيانات:**"
        full_text = f"{header}{ai_answer}{footer}"
        
        if len(full_text) > 4000:
            full_text = full_text[:3900] + "...\n(تم اختصار النص لطوله)" + footer
            
        await update.message.reply_text(full_text, parse_mode='Markdown')
    
    await display_search_results(update, context, results, "الفتاوى المستند إليها", total, is_callback=False, back_callback="search_ai")
    return ConversationHandler.END

async def search_all_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 **البحث الشامل**\n\nأرسل كلمة أو جملة للبحث عنها في العناوين والأسئلة والأجوبة:")
    return BotState.STATE_SEARCH_ALL

async def perform_search_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    public_only = not await bot_db.is_admin(update.effective_user.id)
    context.user_data['current_search_state'] = {
        'type': 'all',
        'params': {'query': query_text, 'public': public_only}
    }
    results, total = await _fetch_contextual_text_fatwas(base_terms=[], user_query=query_text, public_only=public_only, limit=5, offset=0)
    await display_search_results(update, context, results, f"نتائج البحث الشامل عن: {query_text}", total, is_callback=False, back_callback="search_all")
    return ConversationHandler.END

async def search_number_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔢 أرسل رقم الفتوى للبحث عنها مباشرة:")
    return BotState.STATE_SEARCH_NUMBER

async def perform_search_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    num_str = update.message.text.strip()
    if not num_str.isdigit():
        await update.message.reply_text("⚠️ يرجى إرسال رقم صحيح.")
        return BotState.STATE_SEARCH_NUMBER
    
    fatwa = await db.get_fatwa_by_number(int(num_str))
    if not fatwa:
        await update.message.reply_text(f"❌ لم يتم العثور على الفتوى رقم {num_str}")
        return ConversationHandler.END

    from core.utils import format_fatwa_card
    from core.keyboards import create_fatwa_view_keyboard
    is_admin = await bot_db.is_admin(update.effective_user.id)
    await update.message.reply_text(format_fatwa_card(fatwa), reply_markup=create_fatwa_view_keyboard(fatwa, is_admin, False), parse_mode='Markdown')
    return ConversationHandler.END

async def search_title_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🏷️ أرسل عنوان الفتوى أو كلمات منه:")
    return BotState.STATE_SEARCH_TITLE

async def perform_search_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.message.text
    public_only = not await bot_db.is_admin(update.effective_user.id)
    context.user_data['current_search_state'] = {
        'type': 'title',
        'params': {'query': query_text, 'public': public_only}
    }
    results, total = await _fetch_contextual_text_fatwas(base_terms=[], user_query=query_text, public_only=public_only, limit=5, offset=0)
    await display_search_results(update, context, results, f"نتائج البحث عن: {query_text}", total, is_callback=False, back_callback="search_title")
    return ConversationHandler.END

async def search_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    public_only = not await bot_db.is_admin(update.effective_user.id)
    context.user_data['current_search_state'] = {
        'type': 'latest',
        'params': {'public': public_only, 'cap': LATEST_LIMIT}
    }
    results, total = await db.get_all_fatwas(status='published' if public_only else None, limit=5, offset=0)
    await display_search_results(update, context, results, "📅 أحدث الفتاوى", min(total, LATEST_LIMIT), is_callback=True, back_callback="browse_fatwas")
    return BotState.STATE_SEARCH

async def search_popular(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    public_only = not await bot_db.is_admin(update.effective_user.id)
    context.user_data['current_search_state'] = {
        'type': 'popular',
        'params': {'public': public_only, 'cap': POPULAR_LIMIT}
    }
    results, total = await _fetch_popular_fatwas(public_only, limit=5, offset=0, max_total=POPULAR_LIMIT)
    await display_search_results(update, context, results, "🔥 الأكثر مشاهدة", total, is_callback=True, back_callback="browse_fatwas")
    return BotState.STATE_SEARCH

# --- Proxies ---

async def view_fatwa_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.fatwa.view import view_fatwa
    return await view_fatwa(update, context)

async def show_random_fatwa_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.fatwa.view import show_random_fatwa
    return await show_random_fatwa(update, context)

async def continue_reading_fatwa_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.fatwa.view import continue_reading_fatwa
    return await continue_reading_fatwa(update, context)

from handlers.general import cancel_operation, back_to_main

# --- Conversation Handler ---

search_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
        CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),
        CallbackQueryHandler(start_smart_search, pattern='^search_smart$'),
        CallbackQueryHandler(search_ai_prompt, pattern='^search_ai$')
    ],
    states={
        BotState.STATE_SEARCH: [
            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
            CallbackQueryHandler(start_smart_search, pattern='^search_smart$'),
            CallbackQueryHandler(search_ai_prompt, pattern='^search_ai$'),
            CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),
            CallbackQueryHandler(show_random_fatwa_proxy, pattern=r'^random_fatwa(?:_\d+)?$'),
            CallbackQueryHandler(continue_reading_fatwa_proxy, pattern=r'^continue_read_\d+(?:_.+)?$'),
            CallbackQueryHandler(search_number_prompt, pattern='^search_number$'),
            CallbackQueryHandler(search_title_prompt, pattern='^search_title$'),
            CallbackQueryHandler(search_all_prompt, pattern='^search_all$'),
            CallbackQueryHandler(search_scholar, pattern='^search_scholar$'),
            CallbackQueryHandler(search_category, pattern='^search_category$'),
            CallbackQueryHandler(search_source, pattern='^search_source$'),
            
            # Sub-selections
            CallbackQueryHandler(handle_scholar_selection, pattern='^sel_sch_'),
            CallbackQueryHandler(handle_scholar_selection, pattern='^sch_page_'),
            CallbackQueryHandler(handle_category_search_selection, pattern='^sel_cat_'),
            CallbackQueryHandler(handle_category_search_selection, pattern='^cat_page_'),
            CallbackQueryHandler(handle_topic_search_selection, pattern='^sel_top_'),
            CallbackQueryHandler(handle_topic_search_selection, pattern='^cat_all_'),
            CallbackQueryHandler(handle_topic_search_selection, pattern='^top_page_'),
            CallbackQueryHandler(handle_source_selection, pattern='^sel_src_'),
            CallbackQueryHandler(handle_source_selection, pattern='^src_page_'),
            
            CallbackQueryHandler(search_latest, pattern='^search_latest$'),
            CallbackQueryHandler(search_popular, pattern='^search_popular$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        BotState.STATE_SEARCH_AI: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_ai_query),
            CallbackQueryHandler(search_ai_prompt, pattern='^search_ai$'),
            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
            CallbackQueryHandler(cancel_operation, pattern='^cancel$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        BotState.STATE_SEARCH_SMART: [
            CallbackQueryHandler(start_smart_search, pattern='^search_smart$'),
            CallbackQueryHandler(smart_toggle_title, pattern='^smart_toggle_title$'),
            CallbackQueryHandler(smart_toggle_text, pattern='^smart_toggle_text$'),
            CallbackQueryHandler(smart_open_scholars, pattern='^smart_select_scholar$'),
            CallbackQueryHandler(smart_search_now, pattern='^smart_search_now$'),
            CallbackQueryHandler(smart_cancel, pattern='^smart_cancel$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        BotState.STATE_SMART_SEARCH_SCHOLARS: [
            CallbackQueryHandler(handle_smart_scholar_selection, pattern='^smart_sch_'),
            CallbackQueryHandler(start_smart_search, pattern='^search_smart$')
        ],
        BotState.STATE_SMART_SEARCH_QUERY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_smart_search_query),
            CallbackQueryHandler(start_smart_search, pattern='^search_smart$')
        ],
        BotState.STATE_SEARCH_TITLE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_title),
            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        BotState.STATE_SEARCH_ALL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_all),
            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        BotState.STATE_SEARCH_NUMBER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_number),
            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
    },
    fallbacks=[
        CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
        CallbackQueryHandler(cancel_operation, pattern='^cancel$'),
        CommandHandler('cancel', cancel_operation)
    ]
)
