import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import BotState
from core.utils import (
    format_fatwa_content,
    split_long_message,
    escape_markdown,
    safe_reply_text,
    safe_edit_message_text,
    is_valid_url,
    contains_url,
)
from handlers.general import cancel_operation, back_to_main
from .utils import (
    _set_add_step, _pop_add_step, _send_add_prompt, 
    _add_flow_nav_keyboard, _fatwa_text_nav_keyboard,
    _duplicate_fatwa_choice_keyboard, _source_title_keyboard,
    _PENDING_DUPLICATE_ANSWER_KEY, _PENDING_DUPLICATE_MATCHES_KEY
)

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# ==================== إضافة فتوى جديدة ====================

async def start_add_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة فتوى (الخطوة 1: العنوان)"""
    query = update.callback_query
    if query: await query.answer()

    if not await bot_db.is_admin(update.effective_user.id):
        if query: await query.answer("❌ غير مصرح لك.", show_alert=True)
        return ConversationHandler.END

    context.user_data['add_step_history'] = []
    # تنظيف أي حالة بحث سابقة لضمان تجربة إضافة نظيفة
    for key in ['scholar_search_query', 'cat_search_query_1', 'cat_search_query_2', 'selected_topics_slot_1', 'selected_topics_slot_2', 'selected_topics', 'current_cat_id']:
        context.user_data.pop(key, None)
    
    for key in list(context.user_data.keys()):
        if key.startswith('topic_search_query_'):
            context.user_data.pop(key, None)

    _set_add_step(context, "title")
    msg = "📝 **إضافة فتوى جديدة**\n\n📌 أرسل **عنوان الفتوى**:"
    markup = _add_flow_nav_keyboard(include_back_step=False)
    if query: await safe_edit_message_text(query, msg, reply_markup=markup, parse_mode='Markdown')
    else: await safe_reply_text(update, msg, reply_markup=markup, parse_mode='Markdown')
    return BotState.STATE_TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if contains_url(text):
        await safe_reply_text(update, "⚠️ العناوين لا تقبل الروابط. الرجاء إرسال نص العنوان فقط.")
        return BotState.STATE_TITLE

    context.user_data['new_fatwa'] = {'title': text}
    return await show_scholars_step(update, context, page=0)

async def show_scholars_step(update_obj, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE
    _set_add_step(context, "scholar")
    if search_query is None: search_query = context.user_data.get('scholar_search_query')
    
    scholars = await db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = await db.get_scholars_count(search_query=search_query)

    keyboard = []
    row = []
    for s_id, s_name in scholars:
        row.append(InlineKeyboardButton(s_name, callback_data=f"scholar_{s_id}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)

    nav_buttons = []
    if page > 0: nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"scholar_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count: nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"scholar_page_{page+1}"))
    
    if nav_buttons:
        # keyboard.insert(0, nav_buttons) # Top - REMOVED
        pass
        keyboard.append(nav_buttons)    # Bottom

    keyboard.append([InlineKeyboardButton("🔍 بحث عالم", callback_data="search_scholar_add"), InlineKeyboardButton("➕ عالم جديد", callback_data="new_scholar")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])

    msg = f"✅ تم حفظ العنوان.\n\n👤 **اختر العالم** (صفحة {page+1}):"
    await _send_add_prompt(update_obj, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return BotState.STATE_SCHOLAR

async def handle_scholar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    if data == "cancel": return await cancel_operation(update, context)
    if data == "back_main": return await back_to_main(update, context)
    if data == "new_scholar":
        await safe_edit_message_text(query, "👤 أرسل اسم العالم الجديد:", reply_markup=_add_flow_nav_keyboard(include_back_step=True))
        return BotState.STATE_SCHOLAR
    elif data == "search_scholar_add":
        await safe_edit_message_text(query, "🔍 أرسل اسم العالم للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="scholar_search_cancel")], [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]))
        return BotState.STATE_ADD_FATWA_SCHOLAR_SEARCH
    elif data == "scholar_search_cancel":
        context.user_data.pop('scholar_search_query', None)
        await show_scholars_step(update, context, page=0, search_query=None)
        return BotState.STATE_SCHOLAR
    elif data.startswith("scholar_page_"):
        await show_scholars_step(update, context, int(data.split('_')[-1]))
        return BotState.STATE_SCHOLAR
    elif data.startswith("scholar_"):
        scholar_id = int(data.replace("scholar_", "")); scholar_data = await db.get_scholar_by_id(scholar_id)
        scholar_name = scholar_data['name'] if scholar_data else "Unknown Scholar"
        context.user_data['new_fatwa']['scholar_name'] = scholar_name
        _set_add_step(context, "question")
        await safe_edit_message_text(query, f"✅ تم اختيار: {escape_markdown(scholar_name)}\n\n❓ **أرسل نص السؤال**:", reply_markup=_add_flow_nav_keyboard(include_back_step=True), parse_mode='Markdown')
        return BotState.STATE_QUESTION

async def handle_scholar_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = update.message.text.strip(); context.user_data['scholar_search_query'] = search_query
    await show_scholars_step(update, context, page=0, search_query=search_query)
    return BotState.STATE_SCHOLAR

async def receive_scholar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_fatwa']['scholar_name'] = update.message.text
    _set_add_step(context, "question")
    await safe_reply_text(update, "✅ تم حفظ العالم.\n\n❓ **أرسل نص السؤال**:", reply_markup=_add_flow_nav_keyboard(include_back_step=True))
    return BotState.STATE_QUESTION

async def receive_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if contains_url(text):
        await safe_reply_text(update, "⚠️ السؤال لا تقبل الروابط. الرجاء إرسال نص السؤال فقط.", reply_markup=_add_flow_nav_keyboard(include_back_step=True))
        return BotState.STATE_QUESTION
    context.user_data['new_fatwa']['question'] = text
    _set_add_step(context, "answer")
    await safe_reply_text(update, "✅ تم حفظ السؤال.\n\n📄 **أرسل نص الفتوى**:", reply_markup=_fatwa_text_nav_keyboard())
    return BotState.STATE_FATWA_TEXT

async def receive_fatwa_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        await safe_reply_text(update, "⚠️ يرجى إرسال نص الفتوى كرسالة نصية.")
        return BotState.STATE_FATWA_TEXT
    
    text = update.message.text
    if contains_url(text):
        await safe_reply_text(update, "⚠️ نص الفتوى لا تقبل الروابط. الرجاء إرسال النص فقط.", reply_markup=_fatwa_text_nav_keyboard())
        return BotState.STATE_FATWA_TEXT
    parts = context.user_data.setdefault('fatwa_text_parts', [])
    parts.append(text)
    await safe_reply_text(update.message, f"✅ تم استلام الجزء رقم {len(parts)}.\nأرسل باقي النص (إن وجد) أو اضغط **تم الإدخال** للمتابعة.", reply_markup=_fatwa_text_nav_keyboard(), parse_mode='Markdown')
    return BotState.STATE_FATWA_TEXT

async def confirm_fatwa_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    parts = context.user_data.get('fatwa_text_parts', [])
    if not parts:
        await query.answer("⚠️ أرسل نص الفتوى أولاً.", show_alert=True)
        return BotState.STATE_FATWA_TEXT
    answer_text = "\n".join(parts).strip()
    
    import time
    start_time = time.perf_counter()
    duplicates = await db.find_fatwas_by_exact_answer(answer_text, limit=3)
    duration = time.perf_counter() - start_time
    logger.info(f"Duplicate check for fatwa text took {duration:.4f}s")

    if duplicates:
        first_dup = duplicates[0]; fatwa_number = first_dup.get('fatwa_number') or first_dup.get('id')
        title = first_dup.get('title') or "بدون عنوان"; extra_count = max(0, len(duplicates) - 1)
        extra_line = f"\n• يوجد أيضاً {extra_count} نتيجة مشابهة." if extra_count else ""
        context.user_data[_PENDING_DUPLICATE_ANSWER_KEY] = answer_text
        context.user_data[_PENDING_DUPLICATE_MATCHES_KEY] = duplicates
        await safe_edit_message_text(query, f"⚠️ تنبيه: هذه الفتوى موجودة من قبل بنفس النص.\n\n• رقم الفتوى: {fatwa_number}\n• العنوان: {title}{extra_line}\n\nاختر الإجراء:", reply_markup=_duplicate_fatwa_choice_keyboard())
        return BotState.STATE_FATWA_TEXT
    return await _continue_add_after_fatwa_text(update, context, answer_text)

async def _continue_add_after_fatwa_text(update: Update, context: ContextTypes.DEFAULT_TYPE, answer_text: str):
    context.user_data['new_fatwa']['answer'] = answer_text
    context.user_data.pop('fatwa_text_parts', None)
    context.user_data.pop(_PENDING_DUPLICATE_ANSWER_KEY, None)
    context.user_data.pop(_PENDING_DUPLICATE_MATCHES_KEY, None)
    context.user_data['taxonomy_slot'] = 1
    if 'classifications' not in context.user_data['new_fatwa']: context.user_data['new_fatwa']['classifications'] = []
    return await show_categories_step(update, context)

async def handle_duplicate_fatwa_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data or ""
    if data == "dup_cancel_add":
        for key in ['new_fatwa', 'fatwa_text_parts']: context.user_data.pop(key, None)
        from handlers.admin import admin_panel
        await admin_panel(update, context)
        return ConversationHandler.END
    answer_text = context.user_data.get(_PENDING_DUPLICATE_ANSWER_KEY)
    if not answer_text:
        parts = context.user_data.get('fatwa_text_parts', [])
        if not parts: await query.answer("⚠️ أرسل نص الفتوى أولاً.", show_alert=True); return BotState.STATE_FATWA_TEXT
        answer_text = "\n".join(parts).strip()
    return await _continue_add_after_fatwa_text(update, context, answer_text)

async def show_categories_step(update, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE; slot = context.user_data.get('taxonomy_slot', 1)
    if search_query is None: search_query = context.user_data.get(f'cat_search_query_{slot}')
    else: context.user_data[f'cat_search_query_{slot}'] = search_query
    _set_add_step(context, f"category_{slot}")
    cat_type = 'fiqh' if slot == 1 else 'topic'
    cats = await db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type)
    total_count = await db.get_categories_count(search_query=search_query, category_type=cat_type)
    keyboard = []
    row = []
    for cid, name in cats:
        row.append(InlineKeyboardButton(name, callback_data=f"category_{cid}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cat_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"cat_page_{page+1}"))
    
    if nav_row:
        # keyboard.insert(0, nav_row) # Top - REMOVED
        keyboard.append(nav_row)    # Bottom

    keyboard.append([InlineKeyboardButton("🔍 بحث تصنيف", callback_data="search_cat_add"), InlineKeyboardButton("➕ تصنيف جديد", callback_data="add_new_category")])
    if slot == 1: keyboard.append([InlineKeyboardButton("⏭️ تخطي التصنيف الفقهي", callback_data="skip_fiqh_categories")])
    if slot == 2: keyboard.append([InlineKeyboardButton("⏭️ تخطي التصنيف الموضوعي", callback_data="skip_topic_categories")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    label = "الفقهي" if slot == 1 else "الموضوعي"
    msg = f"✅ تم حفظ نص الفتوى.\n\n🏷️ **اختر {label}** (صفحة {page+1}):"
    await _send_add_prompt(update, msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return BotState.STATE_CATEGORIES

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    if data == "back_step": return await handle_back_step(update, context)
    if data == "cancel": return await cancel_operation(update, context)
    if data == "back_main": return await back_to_main(update, context)
    if data == "search_cat_add":
        await safe_edit_message_text(query, "🔍 أرسل اسم التصنيف للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="cat_search_cancel")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
        return BotState.STATE_ADD_FATWA_CAT_SEARCH
    elif data == "cat_search_cancel":
        slot = context.user_data.get('taxonomy_slot', 1); context.user_data.pop(f'cat_search_query_{slot}', None)
        await show_categories_step(update, context, page=0, search_query=None)
        return BotState.STATE_CATEGORIES
    elif data == "skip_topic_categories": return await ask_source(update, context)
    elif data == "skip_fiqh_categories": context.user_data['taxonomy_slot'] = 2; return await show_categories_step(update, context, page=0)
    elif data == "add_new_category":
        await safe_edit_message_text(query, "🏷️ أرسل اسم التصنيف الجديد:", reply_markup=_add_flow_nav_keyboard(include_back_step=True))
        return BotState.STATE_CATEGORIES
    elif data.startswith("cat_page_"): await show_categories_step(update, context, int(data.split('_')[-1])); return BotState.STATE_CATEGORIES
    elif data.startswith("category_"):
        cat_id = int(data.split('_')[-1]); slot = context.user_data.get('taxonomy_slot', 1)
        if context.user_data.get('current_cat_id') != cat_id: context.user_data.pop(f'selected_topics_slot_{slot}', None)
        context.user_data['current_cat_id'] = cat_id
        cat_name = dict(await db.get_categories()).get(cat_id, "")
        label = "الفقهي" if slot == 1 else "الموضوعي"
        await safe_edit_message_text(query, f"✅ تم اختيار التصنيف {label}: {cat_name}")
        return await show_topics_step(query, context, cat_id)

async def handle_category_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = update.message.text.strip(); slot = context.user_data.get('taxonomy_slot', 1); context.user_data[f'cat_search_query_{slot}'] = search_query
    await show_categories_step(update, context, page=0, search_query=search_query)
    return BotState.STATE_CATEGORIES

async def receive_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_name = update.message.text; slot = context.user_data.get('taxonomy_slot', 1); cat_type = 'fiqh' if slot == 1 else 'topic'
    cat_id = await db.add_category(cat_name, category_type=cat_type)
    if not cat_id:
        for cid, name in await db.get_categories():
            if name == cat_name: cat_id = cid; break
    if cat_id: context.user_data['current_cat_id'] = cat_id
    label = "الفقهي" if slot == 1 else "الموضوعي"
    await safe_reply_text(update, f"✅ تم إضافة التصنيف {label}: {cat_name}")
    return await show_topics_step(update, context, cat_id)

async def show_topics_step(update_obj, context, cat_id, page=0, search_query=None):
    ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE; cat_row = await db.get_category(cat_id)
    cat_name = cat_row['name'] if cat_row else "غير محدد"
    if search_query is None: search_query = context.user_data.get(f'topic_search_query_{cat_id}')
    else: context.user_data[f'topic_search_query_{cat_id}'] = search_query
    topics, total_count = await db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    slot = context.user_data.get('taxonomy_slot', 1)
    _set_add_step(context, f"topics_{slot}"); label = "الفقهي" if slot == 1 else "الموضوعي"
    sel = context.user_data.get(f'selected_topics_slot_{slot}', [])
    sel_names = []
    for tid in sel:
        t_row = await db.get_topic(tid)
        if t_row: sel_names.append(t_row['name'])
    sel_text = ", ".join(escape_markdown(n) for n in sel_names) if sel_names else "لا يوجد"
    keyboard = []
    row = []
    for topic in topics:
        tid, name = topic['id'], topic['name']
        row.append(InlineKeyboardButton(f"✅ {name}" if tid in sel else name, callback_data=f"toggle_topic_{tid}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"topic_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"topic_page_{page+1}"))
    
    if nav_row:
        # keyboard.insert(0, nav_row) # Top - REMOVED
        keyboard.append(nav_row)    # Bottom

    keyboard.append([InlineKeyboardButton("🔍 بحث موضوع", callback_data="search_topic"), InlineKeyboardButton("➕ موضوع جديد", callback_data="add_new_topic")])
    keyboard.append([InlineKeyboardButton("✅ إتمام", callback_data="done_topics")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    msg = f"🏷️ التصنيف {label}: **{escape_markdown(cat_name)}**\n✅ **المواضيع المختارة:** {sel_text}\n📑 **اختر المواضيع**:\n\n(اضغط على 'إتمام' عند الانتهاء)"
    await _send_add_prompt(update_obj, msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return BotState.STATE_TOPICS

async def handle_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    slot = context.user_data.get('taxonomy_slot', 1); cat_id = context.user_data.get('current_cat_id')
    if data == "back_step": return await handle_back_step(update, context)
    if data == "cancel": return await cancel_operation(update, context)
    if data == "back_main": return await back_to_main(update, context)
    if data == "search_topic":
        await safe_edit_message_text(query, "🔍 أرسل اسم الموضوع للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="topic_search_cancel")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
        return BotState.STATE_ADD_FATWA_TOPIC_SEARCH
    elif data == "topic_search_cancel":
        if cat_id: context.user_data.pop(f'topic_search_query_{cat_id}', None); await show_topics_step(query, context, cat_id, page=0, search_query=None); return BotState.STATE_TOPICS
        return await show_categories_step(update, context, page=0)
    elif data == "add_new_topic":
        await safe_edit_message_text(query, "📑 أرسل اسم الموضوع الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="topic_page_0")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
        return BotState.STATE_TOPICS
    elif data.startswith("topic_page_"): await show_topics_step(query, context, cat_id, int(data.split('_')[-1])); return BotState.STATE_TOPICS
    elif data.startswith("toggle_topic_"):
        tid = int(data.split('_')[-1]); key = f'selected_topics_slot_{slot}'; sel = context.user_data.setdefault(key, [])
        if tid in sel: sel.remove(tid)
        else: sel.append(tid)
        await show_topics_step(query, context, cat_id); return BotState.STATE_TOPICS
    elif data == "done_topics": return await save_classification_and_continue(query, context, context.user_data.get(f'selected_topics_slot_{slot}', []))

async def handle_back_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    prev = _pop_add_step(context)
    if not prev: return await back_to_main(update, context)
    context.user_data['add_step'] = prev
    if prev == "title": await _send_add_prompt(update, "📝 **إضافة فتوى جديدة**\n\n📌 أرسل **عنوان الفتوى**:", _add_flow_nav_keyboard(include_back_step=False)); return BotState.STATE_TITLE
    if prev == "scholar": return await show_scholars_step(update, context, page=0)
    if prev == "question": await _send_add_prompt(update, "❓ **أرسل نص السؤال**:", _add_flow_nav_keyboard(include_back_step=True)); return BotState.STATE_QUESTION
    if prev == "answer": context.user_data.pop('fatwa_text_parts', None); await _send_add_prompt(update, "📄 **أرسل نص الفتوى**:", _fatwa_text_nav_keyboard()); return BotState.STATE_FATWA_TEXT
    if prev.startswith("category_"): context.user_data['taxonomy_slot'] = int(prev.split('_')[1]); return await show_categories_step(update, context, page=0)
    if prev.startswith("topics_"):
        slot = int(prev.split('_')[1]); context.user_data['taxonomy_slot'] = slot; cat_id = context.user_data.get('current_cat_id')
        if not cat_id: return await show_categories_step(update, context, page=0)
        return await show_topics_step(update, context, cat_id, page=0)
    if prev == "source": return await ask_source(update, context)
    if prev == "source_title": await _send_add_prompt(update, "🎙️ أرسل **عنوان المصدر**:", _source_title_keyboard()); return BotState.STATE_SOURCE_TITLE
    if prev == "source_url": await _send_add_prompt(update, "🔗 أرسل **رابط المصدر**:", InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])); return BotState.STATE_SOURCE_URL
    if prev == "audio": await _send_add_prompt(update, "🔊 أرسل **رابط الصوتية**:", InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_audio")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])); return BotState.STATE_AUDIO
    return await back_to_main(update, context)

async def save_classification_and_continue(update_obj, context, topic_ids):
    cat_id = context.user_data.get('current_cat_id'); slot = context.user_data.get('taxonomy_slot', 1); updated = False
    for cls in context.user_data['new_fatwa']['classifications']:
        if cls['slot_index'] == slot and cls['category_id'] == cat_id: cls['topic_ids'] = topic_ids; updated = True; break
    if not updated: context.user_data['new_fatwa']['classifications'].append({'category_id': cat_id, 'topic_ids': topic_ids, 'slot_index': slot})
    context.user_data.pop(f'selected_topics_slot_{slot}', None)
    if slot == 1: context.user_data['taxonomy_slot'] = 2; return await show_categories_step(update_obj, context)
    return await ask_source(update_obj, context)

async def show_sources_step(update_obj, context, page=0):
    _set_add_step(context, "source"); ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE
    sources = await db.get_sources(limit=ITEMS_PER_PAGE, offset=offset); total = await db.get_sources_count(); keyboard = []
    if sources:
        row = []
        for sid, name in sources:
            row.append(InlineKeyboardButton(f"📚 {name}", callback_data=f"pick_source_{sid}"))
            if len(row) == 2: keyboard.append(row); row = []
        if row: keyboard.append(row)
    else: keyboard.append([InlineKeyboardButton("❌ لا توجد مصادر محفوظة", callback_data="source_manual")])
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"source_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"source_page_{page+1}"))
    
    if nav_row:
        # keyboard.insert(0, nav_row) # Top - REMOVED
        keyboard.append(nav_row)    # Bottom
    keyboard.append([InlineKeyboardButton("✍️ كتابة مصدر جديد", callback_data="source_manual")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    await _send_add_prompt(update_obj, "📚 اختر مصدرًا أو اكتب مصدرًا جديدًا:", reply_markup=InlineKeyboardMarkup(keyboard))
    return BotState.STATE_SOURCE

async def handle_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    if data.startswith("source_page_"): return await show_sources_step(update, context, int(data.split('_')[-1]))
    if data == "source_manual": await safe_edit_message_text(query, "✍️ أرسل اسم المصدر الجديد:", reply_markup=_add_flow_nav_keyboard(include_back_step=True)); return BotState.STATE_SOURCE
    if data.startswith("pick_source_"):
        sid = int(data.split('_')[-1]); src = await db.get_source(sid)
        if not src: return BotState.STATE_SOURCE
        context.user_data['new_fatwa']['source_name'] = src['name']; _set_add_step(context, "source_title")
        await safe_edit_message_text(query, "🎙️ أرسل **عنوان المصدر**:", reply_markup=_source_title_keyboard(), parse_mode='Markdown'); return BotState.STATE_SOURCE_TITLE
    return BotState.STATE_SOURCE

async def ask_source(update_obj, context): return await show_sources_step(update_obj, context, 0)

async def receive_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if contains_url(update.message.text): return BotState.STATE_SOURCE
    context.user_data['new_fatwa']['source_name'] = update.message.text; _set_add_step(context, "source_title")
    await safe_reply_text(update, "🎙️ أرسل **عنوان المصدر**:", reply_markup=_source_title_keyboard()); return BotState.STATE_SOURCE_TITLE

async def receive_source_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if contains_url(update.message.text): return BotState.STATE_SOURCE_TITLE
    context.user_data['new_fatwa']['source_title'] = update.message.text; _set_add_step(context, "source_url")
    await safe_reply_text(update, "🔗 أرسل **رابط المصدر**:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])); return BotState.STATE_SOURCE_URL

async def skip_source_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); context.user_data['new_fatwa']['source_title'] = ""; _set_add_step(context, "source_url")
    await safe_edit_message_text(query, "🔗 أرسل **رابط المصدر**:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])); return BotState.STATE_SOURCE_URL

async def receive_source_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    else:
        if not is_valid_url(update.message.text): return BotState.STATE_SOURCE_URL
        context.user_data['new_fatwa']['source_url'] = update.message.text
    _set_add_step(context, "audio")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_audio")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])
    if query: await safe_edit_message_text(query, "🔊 أرسل **رابط الصوتية**:", reply_markup=markup)
    else: await safe_reply_text(update, "🔊 أرسل **رابط الصوتية**:", reply_markup=markup)
    return BotState.STATE_AUDIO

async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    else:
        if update.message.text != '/skip':
            if not is_valid_url(update.message.text): return BotState.STATE_AUDIO
            context.user_data['new_fatwa']['audio_url'] = update.message.text
    data = context.user_data['new_fatwa']; data['status'] = 'draft'; fid = await db.add_fatwa(data); fatwa = await db.get_fatwa(fid)
    text = "✅ تم حفظ الفتوى بنجاح!\n\n-- معاينة الفتوى --\n\n" + format_fatwa_content(fatwa)
    kb = [[InlineKeyboardButton("📢 نشر الفتوى", callback_data=f"publish_{fid}")], [InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fid}"), InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fid}")], [InlineKeyboardButton("📋 نسخ نص الفتوى", callback_data=f"copy_full_{fid}"), InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")], [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]
    markup = InlineKeyboardMarkup(kb); parts = split_long_message(text)
    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)
        if query:
            if i == 0: await safe_edit_message_text(query, part, reply_markup=markup if is_last else None, disable_web_page_preview=True)
            else: await safe_reply_text(query.message, part, reply_markup=markup if is_last else None, disable_web_page_preview=True)
        else: await safe_reply_text(update, part, reply_markup=markup if is_last else None, disable_web_page_preview=True)
    return ConversationHandler.END

async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip(); cid = context.user_data.get('current_cat_id')
    if not cid: return BotState.STATE_TOPICS
    tid = await db.add_topic(name, cid)
    if tid:
        sel = context.user_data.setdefault('selected_topics', [])
        if tid not in sel: sel.append(tid)
    return await show_topics_step(update, context, cid)

async def handle_topic_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = context.user_data.get('current_cat_id')
    if not cid: return BotState.STATE_TOPICS
    q = update.message.text.strip(); context.user_data[f'topic_search_query_{cid}'] = q
    await show_topics_step(update, context, cid, 0, q); return BotState.STATE_TOPICS

async def handle_taxonomy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(); return BotState.STATE_TAXONOMY_MENU

add_fatwa_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_add_fatwa, pattern="^add_fatwa$")],
    states={
        BotState.STATE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
        BotState.STATE_SCHOLAR: [
            CallbackQueryHandler(handle_scholar_selection, pattern="^(scholar_|scholar_page_|scholar_search_cancel|new_scholar|search_scholar_add|cancel|back_step|back_main)"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar)
        ],
        BotState.STATE_ADD_FATWA_SCHOLAR_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_scholar_search_add)],
        BotState.STATE_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_question), CallbackQueryHandler(handle_back_step, pattern="^back_step$")],
        BotState.STATE_FATWA_TEXT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_fatwa_text),
            CallbackQueryHandler(confirm_fatwa_text, pattern="^confirm_fatwa_text$"),
            CallbackQueryHandler(handle_duplicate_fatwa_choice, pattern="^(dup_continue_add|dup_cancel_add)$"),
            CallbackQueryHandler(handle_back_step, pattern="^back_step$")
        ],
        BotState.STATE_CATEGORIES: [
            CallbackQueryHandler(handle_category_selection, pattern="^(category_|cat_page_|cat_search_cancel|add_new_category|search_cat_add|skip_fiqh_categories|skip_topic_categories|cancel|back_step|back_main)"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category)
        ],
        BotState.STATE_ADD_FATWA_CAT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category_search_add)],
        BotState.STATE_TOPICS: [
            CallbackQueryHandler(handle_topic_selection, pattern="^(toggle_topic_|topic_page_|topic_search_cancel|add_new_topic|search_topic|done_topics|cancel|back_step|back_main)"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_topic)
        ],
        BotState.STATE_ADD_FATWA_TOPIC_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic_search_add)],
        BotState.STATE_SOURCE: [
            CallbackQueryHandler(handle_source_selection, pattern="^(pick_source_|source_page_|source_manual|cancel|back_step)"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source)
        ],
        BotState.STATE_SOURCE_TITLE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source_title),
            CallbackQueryHandler(skip_source_title, pattern="^skip_source_title$"),
            CallbackQueryHandler(handle_back_step, pattern="^back_step$")
        ],
        BotState.STATE_SOURCE_URL: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source_url),
            CallbackQueryHandler(receive_source_url, pattern="^skip_source_url$"),
            CallbackQueryHandler(handle_back_step, pattern="^back_step$")
        ],
        BotState.STATE_AUDIO: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_audio),
            CallbackQueryHandler(receive_audio, pattern="^skip_audio$"),
            CallbackQueryHandler(handle_back_step, pattern="^back_step$")
        ],
    },
    fallbacks=[
        CommandHandler("cancel", cancel_operation),
        CallbackQueryHandler(cancel_operation, pattern="^cancel$")
    ],
    persistent=False,
    name="add_fatwa_conv"
)
