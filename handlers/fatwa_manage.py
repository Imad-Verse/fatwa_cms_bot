import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import *
from core.utils import (
    sanitize_input,
    format_fatwa_content,
    build_fatwa_preview_text,
    split_long_message,
    escape_markdown,
    safe_reply_text,
    safe_edit_message_text,
)
from core.keyboards import (
    create_fatwa_view_keyboard,
    create_published_fatwa_keyboard,
    back_to_main_keyboard as kb_back_main
)
from handlers.general import cancel_operation, start_refresh, back_to_main
from handlers.fatwa_utils import (
    _DELIVERY_LOG_KEY, _PENDING_DUPLICATE_ANSWER_KEY, _PENDING_DUPLICATE_MATCHES_KEY,
    _set_add_step, _pop_add_step, _add_flow_nav_keyboard, _fatwa_text_nav_keyboard,
    _duplicate_fatwa_choice_keyboard, _source_title_keyboard, _send_add_prompt,
    _safe_int, _build_view_back_button, _extract_context_suffix, _resolve_view_context_data,
    _register_delivery_message
)

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# ==================== إضافة فتوى جديدة ====================

async def start_add_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة فتوى (الخطوة 1: العنوان)"""
    query = update.callback_query
    await query.answer()

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ غير مصرح لك.", show_alert=True)
        return ConversationHandler.END

    context.user_data['add_step_history'] = []
    # تنظيف أي حالة بحث سابقة لضمان تجربة إضافة نظيفة
    context.user_data.pop('scholar_search_query', None)
    context.user_data.pop('cat_search_query_1', None)
    context.user_data.pop('cat_search_query_2', None)
    for key in list(context.user_data.keys()):
        if key.startswith('topic_search_query_'):
            context.user_data.pop(key, None)

    # تنظيف أي اختيارات مؤقتة قديمة
    context.user_data.pop('selected_topics_slot_1', None)
    context.user_data.pop('selected_topics_slot_2', None)
    context.user_data.pop('selected_topics', None)
    context.user_data.pop('current_cat_id', None)

    _set_add_step(context, "title")
    await query.edit_message_text(
        "📝 **إضافة فتوى جديدة**\n\n📌 أرسل **عنوان الفتوى**:",
        reply_markup=_add_flow_nav_keyboard(include_back_step=False)
    )
    return STATE_TITLE

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    text = update.message.text
    if contains_url(text):
        await update.message.reply_text("⚠️ العناوين لا تقبل الروابط. الرجاء إرسال نص العنوان فقط.")
        return STATE_TITLE

    context.user_data['new_fatwa'] = {'title': text}
    return await show_scholars_step(update, context, page=0)

async def show_scholars_step(update_obj, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    _set_add_step(context, "scholar")

    if search_query is None:
        search_query = context.user_data.get('scholar_search_query')

    scholars = db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = db.get_scholars_count(search_query=search_query)

    keyboard = []
    row = []
    for s_id, s_name in scholars:
        row.append(InlineKeyboardButton(s_name, callback_data=f"scholar_{s_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"scholar_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"scholar_page_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔍 بحث عالم", callback_data="search_scholar_add"),
        InlineKeyboardButton("➕ عالم جديد", callback_data="new_scholar")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"),
        InlineKeyboardButton("❌ إلغاء", callback_data="cancel")
    ])

    msg = f"✅ تم حفظ العنوان.\n\n👤 **اختر العالم** (صفحة {page+1}):"
    reply_markup = InlineKeyboardMarkup(keyboard)
    await _send_add_prompt(update_obj, msg, reply_markup=reply_markup, parse_mode='Markdown')
    return STATE_SCHOLAR

async def handle_scholar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel":
        return await cancel_operation(update, context)
    if data == "back_main":
        return await back_to_main(update, context)
    if data == "new_scholar":
        await query.edit_message_text(
            "👤 أرسل اسم العالم الجديد:",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_SCHOLAR
    elif data == "search_scholar_add":
        await query.edit_message_text(
            "🔍 أرسل اسم العالم للبحث عنه:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء البحث", callback_data="scholar_search_cancel")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
            ])
        )
        return STATE_ADD_FATWA_SCHOLAR_SEARCH
    elif data == "scholar_search_cancel":
        context.user_data.pop('scholar_search_query', None)
        await show_scholars_step(update, context, page=0, search_query=None)
        return STATE_SCHOLAR
    elif data.startswith("scholar_page_"):
        page = int(data.split('_')[-1])
        await show_scholars_step(update, context, page)
        return STATE_SCHOLAR
    elif data.startswith("scholar_"):
        scholar_id = int(data.replace("scholar_", ""))
        scholar_data = db.get_scholar_by_id(scholar_id)
        scholar_name = scholar_data['name'] if scholar_data else "Unknown Scholar"
        context.user_data['new_fatwa']['scholar_name'] = scholar_name
        _set_add_step(context, "question")
        display_scholar = escape_markdown(scholar_name)
        await query.edit_message_text(
            f"✅ تم اختيار: {display_scholar}\n\n❓ **أرسل نص السؤال**:",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True),
            parse_mode='Markdown'
        )
        return STATE_QUESTION

async def handle_scholar_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = update.message.text.strip()
    context.user_data['scholar_search_query'] = search_query
    await show_scholars_step(update, context, page=0, search_query=search_query)
    return STATE_SCHOLAR

async def receive_scholar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_fatwa']['scholar_name'] = update.message.text
    _set_add_step(context, "question")
    await update.message.reply_text(
        "✅ تم حفظ العالم.\n\n❓ **أرسل نص السؤال**:",
        reply_markup=_add_flow_nav_keyboard(include_back_step=True)
    )
    return STATE_QUESTION

async def receive_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    text = update.message.text
    if contains_url(text):
        await update.message.reply_text(
            "⚠️ السؤال لا يقبل الروابط. الرجاء إرسال نص السؤال فقط.",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_QUESTION
    context.user_data['new_fatwa']['question'] = text
    _set_add_step(context, "answer")
    await update.message.reply_text(
        "✅ تم حفظ السؤال.\n\n📄 **أرسل نص الفتوى**:",
        reply_markup=_fatwa_text_nav_keyboard()
    )
    return STATE_FATWA_TEXT

async def receive_fatwa_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    text = update.message.text
    if contains_url(text):
        await update.message.reply_text(
            "⚠️ نص الفتوى لا يقبل الروابط. الرجاء إرسال النص فقط.",
            reply_markup=_fatwa_text_nav_keyboard()
        )
        return STATE_FATWA_TEXT

    context.user_data.pop(_PENDING_DUPLICATE_ANSWER_KEY, None)
    context.user_data.pop(_PENDING_DUPLICATE_MATCHES_KEY, None)
    parts = context.user_data.setdefault('fatwa_text_parts', [])
    parts.append(text)
    await safe_reply_text(
        update.message,
        f"✅ تم استلام الجزء رقم {len(parts)}.\n"
        "أرسل باقي النص (إن وجد) أو اضغط **تم الإدخال** للمتابعة.",
        reply_markup=_fatwa_text_nav_keyboard(),
        parse_mode='Markdown'
    )
    return STATE_FATWA_TEXT

async def confirm_fatwa_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = context.user_data.get('fatwa_text_parts', [])
    if not parts:
        await query.answer("⚠️ أرسل نص الفتوى أولاً.", show_alert=True)
        return STATE_FATWA_TEXT

    answer_text = "\n".join(parts).strip()
    duplicates = db.find_fatwas_by_exact_answer(answer_text, limit=3)

    if duplicates:
        first_dup = duplicates[0]
        fatwa_number = first_dup.get('fatwa_number') or first_dup.get('id')
        title = first_dup.get('title') or "بدون عنوان"
        extra_count = max(0, len(duplicates) - 1)
        extra_line = f"\n• يوجد أيضاً {extra_count} نتيجة مشابهة." if extra_count else ""
        context.user_data[_PENDING_DUPLICATE_ANSWER_KEY] = answer_text
        context.user_data[_PENDING_DUPLICATE_MATCHES_KEY] = duplicates
        await query.edit_message_text(
            "⚠️ تنبيه: هذه الفتوى موجودة من قبل بنفس النص.\n\n"
            f"• رقم الفتوى: {fatwa_number}\n"
            f"• العنوان: {title}"
            f"{extra_line}\n\n"
            "اختر الإجراء:",
            reply_markup=_duplicate_fatwa_choice_keyboard()
        )
        return STATE_FATWA_TEXT
    return await _continue_add_after_fatwa_text(update, context, answer_text)

async def _continue_add_after_fatwa_text(update: Update, context: ContextTypes.DEFAULT_TYPE, answer_text: str):
    context.user_data['new_fatwa']['answer'] = answer_text
    context.user_data.pop('fatwa_text_parts', None)
    context.user_data.pop(_PENDING_DUPLICATE_ANSWER_KEY, None)
    context.user_data.pop(_PENDING_DUPLICATE_MATCHES_KEY, None)
    context.user_data['taxonomy_slot'] = 1
    if 'classifications' not in context.user_data['new_fatwa']:
        context.user_data['new_fatwa']['classifications'] = []
    return await show_categories_step(update, context)

async def handle_duplicate_fatwa_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == "dup_cancel_add":
        context.user_data.pop('new_fatwa', None)
        context.user_data.pop('fatwa_text_parts', None)
        from handlers.admin import admin_panel
        await admin_panel(update, context)
        return ConversationHandler.END
    answer_text = context.user_data.get(_PENDING_DUPLICATE_ANSWER_KEY)
    if not answer_text:
        parts = context.user_data.get('fatwa_text_parts', [])
        if not parts:
            await query.answer("⚠️ أرسل نص الفتوى أولاً.", show_alert=True)
            return STATE_FATWA_TEXT
        answer_text = "\n".join(parts).strip()
    return await _continue_add_after_fatwa_text(update, context, answer_text)

async def show_categories_step(update, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE
    slot = context.user_data.get('taxonomy_slot', 1)
    if search_query is None:
        search_query = context.user_data.get(f'cat_search_query_{slot}')
    else:
        context.user_data[f'cat_search_query_{slot}'] = search_query
    _set_add_step(context, f"category_{slot}")
    cat_type = 'fiqh' if slot == 1 else 'topic'
    cats = db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type)
    total_count = db.get_categories_count(search_query=search_query, category_type=cat_type)

    keyboard = []
    row = []
    for cid, name in cats:
        row.append(InlineKeyboardButton(name, callback_data=f"category_{cid}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cat_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"cat_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔍 بحث تصنيف", callback_data="search_cat_add"),
        InlineKeyboardButton("➕ تصنيف جديد", callback_data="add_new_category")
    ])
    if slot == 1:
        keyboard.append([InlineKeyboardButton("⏭️ تخطي التصنيف الفقهي", callback_data="skip_fiqh_categories")])
    if slot == 2:
        keyboard.append([InlineKeyboardButton("⏭️ تخطي التصنيف الموضوعي", callback_data="skip_topic_categories")])

    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    label = "الفقهي" if slot == 1 else "الموضوعي"
    msg_text = f"✅ تم حفظ نص الفتوى.\n\n🏷️ **اختر {label}** (صفحة {page+1}):"
    await _send_add_prompt(update, msg_text, reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_CATEGORIES

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "back_step": return await handle_back_step(update, context)
    if data == "cancel": return await cancel_operation(update, context)
    if data == "back_main": return await back_to_main(update, context)

    if data == "search_cat_add":
        await query.edit_message_text(
            "🔍 أرسل اسم التصنيف للبحث عنه:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء البحث", callback_data="cat_search_cancel")],
                [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
            ])
        )
        return STATE_ADD_FATWA_CAT_SEARCH
    elif data == "cat_search_cancel":
        slot = context.user_data.get('taxonomy_slot', 1)
        context.user_data.pop(f'cat_search_query_{slot}', None)
        await show_categories_step(update, context, page=0, search_query=None)
        return STATE_CATEGORIES
    elif data == "skip_topic_categories":
        return await ask_source(update, context)
    elif data == "skip_fiqh_categories":
        context.user_data['taxonomy_slot'] = 2
        return await show_categories_step(update, context, page=0)
    elif data == "add_new_category":
        await query.edit_message_text(
            "🏷️ أرسل اسم التصنيف الجديد:",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_CATEGORIES
    elif data.startswith("cat_page_"):
        page = int(data.split('_')[-1])
        await show_categories_step(update, context, page)
        return STATE_CATEGORIES
    elif data.startswith("category_"):
        cat_id = int(data.split('_')[-1])
        slot = context.user_data.get('taxonomy_slot', 1)
        prev_cat_id = context.user_data.get('current_cat_id')
        if prev_cat_id and prev_cat_id != cat_id:
            context.user_data.pop(f'selected_topics_slot_{slot}', None)
        context.user_data['current_cat_id'] = cat_id
        cat_name = dict(db.get_categories()).get(cat_id, "")
        label = "الفقهي" if slot == 1 else "الموضوعي"
        await query.edit_message_text(f"✅ تم اختيار التصنيف {label}: {cat_name}")
        return await show_topics_step(query, context, cat_id)

async def handle_category_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = update.message.text.strip()
    slot = context.user_data.get('taxonomy_slot', 1)
    context.user_data[f'cat_search_query_{slot}'] = search_query
    await show_categories_step(update, context, page=0, search_query=search_query)
    return STATE_CATEGORIES

async def receive_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_name = update.message.text
    slot = context.user_data.get('taxonomy_slot', 1)
    cat_type = 'fiqh' if slot == 1 else 'topic'
    cat_id = db.add_category(cat_name, category_type=cat_type)
    if not cat_id:
        cats = db.get_categories()
        for cid, name in cats:
            if name == cat_name:
                cat_id = cid
                break
    if cat_id: context.user_data['current_cat_id'] = cat_id
    label = "الفقهي" if slot == 1 else "الموضوعي"
    await update.message.reply_text(f"✅ تم إضافة التصنيف {label}: {cat_name}")
    return await show_topics_step(update, context, cat_id)

async def show_topics_step(update_obj, context, cat_id, page=0, search_query=None):
    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE
    cat_row = db.get_category(cat_id)
    cat_name = cat_row['name'] if cat_row else "غير محدد"
    if search_query is None: search_query = context.user_data.get(f'topic_search_query_{cat_id}')
    else: context.user_data[f'topic_search_query_{cat_id}'] = search_query
    topics = db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = db.get_topics_count(cat_id, search_query=search_query)
    slot = context.user_data.get('taxonomy_slot', 1)
    _set_add_step(context, f"topics_{slot}")
    label = "الفقهي" if slot == 1 else "الموضوعي"
    selected_topics = context.user_data.get(f'selected_topics_slot_{slot}', [])
    selected_topic_names = []
    for tid in selected_topics:
        topic_row = db.get_topic(tid)
        if topic_row and topic_row.get("name"): selected_topic_names.append(topic_row["name"])
    selected_topics_text = ", ".join(escape_markdown(name) for name in selected_topic_names) if selected_topic_names else "لا يوجد"

    keyboard = []
    row = []
    for tid, name in topics:
        text = f"✅ {name}" if tid in selected_topics else name
        row.append(InlineKeyboardButton(text, callback_data=f"toggle_topic_{tid}"))
        if len(row) == 2: keyboard.append(row); row = []
    if row: keyboard.append(row)

    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"topic_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count: nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"topic_page_{page+1}"))
    if nav: keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🔍 بحث موضوع", callback_data="search_topic"), InlineKeyboardButton("➕ موضوع جديد", callback_data="add_new_topic")])
    keyboard.append([InlineKeyboardButton("✅ إتمام", callback_data="done_topics")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    msg_text = f"🏷️ التصنيف {label}: **{escape_markdown(cat_name)}**\n✅ **المواضيع المختارة:** {selected_topics_text}\n📑 **اختر المواضيع**:\n\n(اضغط على 'إتمام' عند الانتهاء)"
    await _send_add_prompt(update_obj, msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return STATE_TOPICS

async def handle_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    slot = context.user_data.get('taxonomy_slot', 1)
    cat_id = context.user_data.get('current_cat_id')
    if data == "back_step": return await handle_back_step(update, context)
    if data == "cancel": return await cancel_operation(update, context)
    if data == "back_main": return await back_to_main(update, context)

    if data == "search_topic":
        await query.edit_message_text("🔍 أرسل اسم الموضوع للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="topic_search_cancel")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
        return STATE_ADD_FATWA_TOPIC_SEARCH
    elif data == "topic_search_cancel":
        if cat_id:
            context.user_data.pop(f'topic_search_query_{cat_id}', None)
            await show_topics_step(query, context, cat_id, page=0, search_query=None)
            return STATE_TOPICS
        return await show_categories_step(update, context, page=0)
    elif data == "add_new_topic":
        await query.edit_message_text("📑 أرسل اسم الموضوع الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="topic_page_0")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
        return STATE_TOPICS
    elif data.startswith("topic_page_"):
        await show_topics_step(query, context, cat_id, int(data.split('_')[-1]))
        return STATE_TOPICS
    elif data.startswith("toggle_topic_"):
        topic_id = int(data.split('_')[-1])
        key = f'selected_topics_slot_{slot}'
        sel = context.user_data.setdefault(key, [])
        if topic_id in sel: sel.remove(topic_id)
        else: sel.append(topic_id)
        await show_topics_step(query, context, cat_id)
        return STATE_TOPICS
    elif data == "done_topics":
        topic_ids = context.user_data.get(f'selected_topics_slot_{slot}', [])
        return await save_classification_and_continue(query, context, topic_ids)

async def handle_back_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    prev_step = _pop_add_step(context)
    if not prev_step: return await back_to_main(update, context)
    context.user_data['add_step'] = prev_step

    if prev_step == "title": await _send_add_prompt(update, "📝 **إضافة فتوى جديدة**\n\n📌 أرسل **عنوان الفتوى**:", _add_flow_nav_keyboard(include_back_step=False)); return STATE_TITLE
    if prev_step == "scholar": return await show_scholars_step(update, context, page=0)
    if prev_step == "question": await _send_add_prompt(update, "❓ **أرسل نص السؤال**:", _add_flow_nav_keyboard(include_back_step=True)); return STATE_QUESTION
    if prev_step == "answer":
        context.user_data.pop('fatwa_text_parts', None)
        await _send_add_prompt(update, "📄 **أرسل نص الفتوى**:", _fatwa_text_nav_keyboard())
        return STATE_FATWA_TEXT
    if prev_step.startswith("category_"):
        slot = int(prev_step.split('_')[1]); context.user_data['taxonomy_slot'] = slot
        return await show_categories_step(update, context, page=0)
    if prev_step.startswith("topics_"):
        slot = int(prev_step.split('_')[1]); context.user_data['taxonomy_slot'] = slot
        cat_id = context.user_data.get('current_cat_id')
        if not cat_id: return await show_categories_step(update, context, page=0)
        return await show_topics_step(update, context, cat_id, page=0)
    if prev_step == "source": return await ask_source(update, context)
    if prev_step == "source_title": await _send_add_prompt(update, "🎙️ أرسل **عنوان المصدر**:", _source_title_keyboard()); return STATE_SOURCE_TITLE
    if prev_step == "source_url": await _send_add_prompt(update, "🔗 أرسل **رابط المصدر**:", InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])); return STATE_SOURCE_URL
    if prev_step == "audio": await _send_add_prompt(update, "🔊 أرسل **رابط الصوتية**:", InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_audio")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])); return STATE_AUDIO
    return await back_to_main(update, context)

async def save_classification_and_continue(update_obj, context, topic_ids):
    cat_id = context.user_data.get('current_cat_id')
    slot = context.user_data.get('taxonomy_slot', 1)
    updated = False
    for cls in context.user_data['new_fatwa']['classifications']:
        if cls['slot_index'] == slot and cls['category_id'] == cat_id:
            cls['topic_ids'] = topic_ids; updated = True; break
    if not updated:
        context.user_data['new_fatwa']['classifications'].append({'category_id': cat_id, 'topic_ids': topic_ids, 'slot_index': slot})
    context.user_data.pop(f'selected_topics_slot_{slot}', None)
    if slot == 1:
        context.user_data['taxonomy_slot'] = 2
        return await show_categories_step(update_obj, context)
    return await ask_source(update_obj, context)

async def show_sources_step(update_obj, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    _set_add_step(context, "source")
    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE
    sources = db.get_sources(limit=ITEMS_PER_PAGE, offset=offset)
    total_count = db.get_sources_count()
    keyboard = []
    if sources:
        row = []
        for sid, name in sources:
            row.append(InlineKeyboardButton(f"📚 {name}", callback_data=f"pick_source_{sid}"))
            if len(row) == 2: keyboard.append(row); row = []
        if row: keyboard.append(row)
    else: keyboard.append([InlineKeyboardButton("❌ لا توجد مصادر محفوظة", callback_data="source_manual")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"source_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count: nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"source_page_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("✍️ كتابة مصدر جديد", callback_data="source_manual")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")])
    msg = "📚 اختر مصدرًا أو اكتب مصدرًا جديدًا:"
    await _send_add_prompt(update_obj, msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_SOURCE

async def handle_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    if data.startswith("source_page_"): return await show_sources_step(update, context, page=int(data.split('_')[-1]))
    if data == "source_manual":
        await query.edit_message_text("✍️ أرسل اسم المصدر الجديد:", reply_markup=_add_flow_nav_keyboard(include_back_step=True))
        return STATE_SOURCE
    if data.startswith("pick_source_"):
        source_id = int(data.split('_')[-1]); source = db.get_source(source_id)
        if not source: return STATE_SOURCE
        context.user_data['new_fatwa']['source_name'] = source['name']
        _set_add_step(context, "source_title")
        await query.edit_message_text("🎙️ أرسل **عنوان المصدر**:", reply_markup=_source_title_keyboard(), parse_mode='Markdown')
        return STATE_SOURCE_TITLE
    return STATE_SOURCE

async def ask_source(update_obj, context: ContextTypes.DEFAULT_TYPE):
    return await show_sources_step(update_obj, context, page=0)

async def receive_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    if contains_url(update.message.text): return STATE_SOURCE
    context.user_data['new_fatwa']['source_name'] = update.message.text
    _set_add_step(context, "source_title")
    await update.message.reply_text("🎙️ أرسل **عنوان المصدر**:", reply_markup=_source_title_keyboard())
    return STATE_SOURCE_TITLE

async def receive_source_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    if contains_url(update.message.text): return STATE_SOURCE_TITLE
    context.user_data['new_fatwa']['source_title'] = update.message.text
    _set_add_step(context, "source_url")
    await update.message.reply_text("🔗 أرسل **رابط المصدر**:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
    return STATE_SOURCE_URL

async def skip_source_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    context.user_data['new_fatwa']['source_title'] = ""
    _set_add_step(context, "source_url")
    await query.edit_message_text("🔗 أرسل **رابط المصدر**:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]]))
    return STATE_SOURCE_URL

async def receive_source_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    else:
        if not is_valid_url(update.message.text): return STATE_SOURCE_URL
        context.user_data['new_fatwa']['source_url'] = update.message.text
    _set_add_step(context, "audio")
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("⏭️ تخطي", callback_data="skip_audio")], [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]])
    msg = "🔊 أرسل **رابط الصوتية**:"
    if query: await query.edit_message_text(msg, reply_markup=markup)
    else: await update.message.reply_text(msg, reply_markup=markup)
    return STATE_AUDIO

async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query: await query.answer()
    else:
        if update.message.text != '/skip':
            if not is_valid_url(update.message.text): return STATE_AUDIO
            context.user_data['new_fatwa']['audio_url'] = update.message.text
    fatwa_data = context.user_data['new_fatwa']; fatwa_data['status'] = 'draft'
    fatwa_id = db.add_fatwa(fatwa_data); fatwa = db.get_fatwa(fatwa_id)
    text = "✅ تم حفظ الفتوى بنجاح!\n\n-- معاينة الفتوى --\n\n" + format_fatwa_content(fatwa)
    keyboard = [[InlineKeyboardButton("📢 نشر الفتوى", callback_data=f"publish_{fatwa_id}")], [InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"), InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")], [InlineKeyboardButton("📋 نسخ نص الفتوى", callback_data=f"copy_full_{fatwa_id}"), InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")], [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    parts = split_long_message(text)
    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)
        if query:
            if i == 0: await query.edit_message_text(part, reply_markup=reply_markup if is_last else None, disable_web_page_preview=True)
            else: await query.message.reply_text(part, reply_markup=reply_markup if is_last else None, disable_web_page_preview=True)
        else: await update.message.reply_text(part, reply_markup=reply_markup if is_last else None, disable_web_page_preview=True)
    return ConversationHandler.END

async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip(); cat_id = context.user_data.get('current_cat_id')
    if not cat_id: return STATE_TOPICS
    topic_id = db.add_topic(name, cat_id)
    if topic_id:
        sel = context.user_data.setdefault('selected_topics', [])
        if topic_id not in sel: sel.append(topic_id)
    return await show_topics_step(update, context, cat_id)

async def handle_topic_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_id = context.user_data.get('current_cat_id')
    if not cat_id: return STATE_TOPICS
    query = update.message.text.strip(); context.user_data[f'topic_search_query_{cat_id}'] = query
    await show_topics_step(update, context, cat_id, page=0, search_query=query)
    return STATE_TOPICS

async def handle_taxonomy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(); return STATE_TAXONOMY_MENU

# ==================== عمليات النشر والحذف ====================

async def delete_fatwa_from_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer("جاري الحذف من الكل...")
    if not bot_db.is_admin(update.effective_user.id): return
    fatwa_id = int(query.data.split('_')[-1])
    app = getattr(context, "application", None)
    if not app: return
    store = app.bot_data.get(_DELIVERY_LOG_KEY, {})
    fatwa_store = store.get(str(fatwa_id), {})
    if not fatwa_store:
        await query.message.reply_text("ℹ️ لا توجد رسائل محفوظة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]))
        return
    deleted, failed = 0, 0
    for chat_id_raw, msg_ids in fatwa_store.items():
        for msg_id in msg_ids:
            try: await context.bot.delete_message(chat_id=int(chat_id_raw), message_id=int(msg_id)); deleted += 1
            except Exception: failed += 1
    store.pop(str(fatwa_id), None)
    await query.message.reply_text(f"✅ تم تنفيذ الحذف.\n🗑️ حُذفت: {deleted}\n⚠️ فشلت: {failed}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]))

async def publish_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not bot_db.is_admin(update.effective_user.id): return
    fatwa_id = int(query.data.split('_')[1])
    db.update_fatwa(fatwa_id, {'status': 'published'})
    await query.answer("✅ تم النشر!")
    fatwa = db.get_fatwa(fatwa_id)
    text = format_fatwa_content(fatwa)
    keyboard = [[InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"), InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")], [InlineKeyboardButton("📢 إرسال الفتوى", callback_data=f"broadcast_{fatwa_id}")], [InlineKeyboardButton("📋 نسخ النص", callback_data=f"copy_full_{fatwa_id}"), InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_fatwa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; fatwa_id = int(query.data.split('_')[2])
    await query.edit_message_text("⚠️ **هل أنت متأكد؟**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ نعم، احذف", callback_data=f"delete_final_{fatwa_id}")], [InlineKeyboardButton("❌ إلغاء", callback_data=f"view_{fatwa_id}")]]), parse_mode='Markdown')

async def delete_fatwa_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; fatwa_id = int(query.data.split('_')[2])
    db.delete_fatwa(fatwa_id); bot_db.remove_favorites_for_fatwa(fatwa_id)
    await query.answer("🗑️ تم الحذف"); await query.edit_message_text("✅ تم حذف الفتوى.", reply_markup=kb_back_main())

# ==================== تعديل الفتوى ====================

def _format_edit_current_value(value, limit: int = 900) -> str:
    if not value: return "— لا يوجد —"
    text = str(value).strip()
    return text[:limit] + "..." if len(text) > limit else text

def _pair_buttons(buttons: list) -> list:
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]

def _format_edit_slot_summary(fatwa: dict, slot: int) -> str:
    cls = [c for c in fatwa.get('classifications', []) if c.get('slot_index') == slot]
    if not cls: return "لا توجد تصنيفات مختارة."
    return "\n\n".join([f"{i}. {c.get('category_name') or 'غير محدد'}\n   المواضيع: {', '.join(c.get('topic_names') or []) or 'بدون مواضيع'}" for i, c in enumerate(cls, 1)])

def _build_edit_field_prompt(fatwa: dict, field: str, back_cb: str) -> tuple:
    labels = {"title": "العنوان", "scholar_name": "العالم", "question": "السؤال", "answer": "النص", "source_name": "المصدر", "source_title": "عنوان المصدر", "source_url": "رابط المصدر", "audio_url": "الرابط الصوتي"}
    prompts = {"title": "أرسل العنوان الجديد:", "scholar_name": "أرسل اسم العالم الجديد:", "question": "أرسل نص السؤال الجديد:", "answer": "أرسل نص الفتوى الجديد:", "source_name": "أرسل اسم المصدر الجديد:", "source_title": "أرسل عنوان المصدر الجديد:", "source_url": "أرسل رابط المصدر الجديد:", "audio_url": "أرسل الرابط الصوتي الجديد:"}
    clear_actions = {"source_name": "edit_clear_source_name", "source_title": "edit_clear_source_title", "source_url": "edit_clear_source_url", "audio_url": "edit_clear_audio_url"}
    label = labels.get(field, "الحقل")
    text = f"✏️ تعديل {label}\n\nالقيمة الحالية:\n{_format_edit_current_value(fatwa.get(field))}\n\n{prompts.get(field)}"
    kb = []
    if field in clear_actions: kb.append([InlineKeyboardButton("🗑️ حذف الحالي", callback_data=clear_actions[field])])
    kb.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=back_cb)])
    return text, InlineKeyboardMarkup(kb)

async def _show_edit_scholar_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query; fatwa_id = context.user_data.get('edit_fatwa_id')
    fatwa = db.get_fatwa(fatwa_id) if fatwa_id else None
    if not fatwa: return
    ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE
    scholars = db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset); total = db.get_scholars_count()
    current = str(fatwa.get('scholar_name') or "").strip()
    kb = []
    for sid, name in scholars: kb.append(InlineKeyboardButton(f"✅ {name}" if name == current else name, callback_data=f"edit_pick_scholar_{sid}"))
    keyboard = _pair_buttons(kb)
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"edit_sch_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total: nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"edit_sch_page_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("➕ إضافة عالم جديد", callback_data="edit_add_new_scholar")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=f"edit_fatwa_{fatwa_id}")])
    text = f"👤 تعديل العالم\n\nالعالم الحالي: {current}\n\nاختر من القائمة:"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_EDIT_MENU

async def start_edit_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE, fatwa_id: int = None):
    query = update.callback_query
    if query:
        await query.answer()
        if query.data and query.data.startswith('edit_fatwa_'):
            fatwa_id = int(query.data.split('_')[2])
    if fatwa_id is None: fatwa_id = context.user_data.get('edit_fatwa_id')
    context.user_data['edit_fatwa_id'] = fatwa_id
    kb = [[InlineKeyboardButton("العنوان", callback_data="edit_field_title"), InlineKeyboardButton("العالم", callback_data="edit_field_scholar")], [InlineKeyboardButton("السؤال", callback_data="edit_field_question"), InlineKeyboardButton("النص", callback_data="edit_field_text")], [InlineKeyboardButton("🏷️ التصنيف الفقهي", callback_data="edit_slot_1"), InlineKeyboardButton("🏷️ التصنيف الموضوعي", callback_data="edit_slot_2")], [InlineKeyboardButton("المصدر", callback_data="edit_field_source_name"), InlineKeyboardButton("عنوان المصدر", callback_data="edit_field_source_title")], [InlineKeyboardButton("رابط المصدر", callback_data="edit_field_source_url"), InlineKeyboardButton("الرابط الصوتي", callback_data="edit_field_audio")], [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_edit")]]
    text = "✏️ **تعديل الفتوى**\n\nاختر الحقل المراد تعديله:"
    if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return STATE_EDIT_MENU

async def handle_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    fatwa_id = context.user_data.get('edit_fatwa_id')
    if data == "cancel_edit":
        from handlers.fatwa_view import view_fatwa
        await view_fatwa(update, context, fatwa_id=fatwa_id); return ConversationHandler.END
    if data.startswith("edit_fatwa_"): return await start_edit_fatwa(update, context, fatwa_id=int(data.split('_')[-1]))
    if data.startswith("edit_sch_page_"): return await _show_edit_scholar_picker(update, context, page=int(data.split('_')[-1]))
    if data.startswith("edit_pick_scholar_"):
        sch = db.get_scholar_by_id(int(data.split('_')[-1]))
        db.update_fatwa(fatwa_id, {'scholar_name': sch['name']}); return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
    if data == "edit_add_new_scholar":
        context.user_data['edit_field'] = "scholar_name"
        text, markup = _build_edit_field_prompt(db.get_fatwa(fatwa_id), "scholar_name", f"edit_fatwa_{fatwa_id}")
        await query.edit_message_text(text, reply_markup=markup); return STATE_EDIT_VALUE
    if data.startswith("edit_slot_"):
        slot = int(data.split('_')[-1]); context.user_data['edit_taxonomy_slot'] = slot
        summary = _format_edit_slot_summary(db.get_fatwa(fatwa_id), slot)
        actions = [InlineKeyboardButton("تغيير التصنيف الحالي", callback_data=f"edit_tax_cat_{slot}"), InlineKeyboardButton("تعديل المواضيع", callback_data=f"edit_tax_top_{slot}"), InlineKeyboardButton("➕ إضافة تصنيف آخر", callback_data=f"add_another_cat_{slot}"), InlineKeyboardButton("🗑️ حذف كافة التصنيفات", callback_data="delete_all_fatwa_classifications")]
        if slot == 2: actions.append(InlineKeyboardButton("🗑️ حذف هذا النوع بالكامل", callback_data="delete_slot_2"))
        kb = _pair_buttons(actions)
        kb.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"edit_fatwa_{fatwa_id}")])
        await query.edit_message_text(f"🏷️ تعديل النوع {slot}\n\nالتصنيفات الحالية:\n{summary}\n\nاختر الإجراء:", reply_markup=InlineKeyboardMarkup(kb)); return STATE_EDIT_MENU
    field_map = {"edit_field_title": "title", "edit_field_scholar": "scholar_name", "edit_field_question": "question", "edit_field_text": "answer", "edit_field_source_name": "source_name", "edit_field_source_title": "source_title", "edit_field_source_url": "source_url", "edit_field_audio": "audio_url"}
    if data in field_map:
        f = field_map[data]; context.user_data['edit_field'] = f
        text, markup = _build_edit_field_prompt(db.get_fatwa(fatwa_id), f, f"edit_fatwa_{fatwa_id}")
        await query.edit_message_text(text, reply_markup=markup); return STATE_EDIT_VALUE
    return STATE_EDIT_MENU

async def receive_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get('edit_field'); fatwa_id = context.user_data.get('edit_fatwa_id')
    db.update_fatwa(fatwa_id, {field: update.message.text})
    await update.message.reply_text("✅ تم التحديث."); return ConversationHandler.END

async def handle_edit_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    fatwa_id = context.user_data.get('edit_fatwa_id')
    if data == "search_edit_cat":
        await query.edit_message_text("🔍 أرسل اسم التصنيف للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="edit_cat_search_cancel")]]))
        return STATE_EDIT_CAT_SEARCH
    if data == "add_new_edit_cat":
        await query.edit_message_text("🏷️ أرسل اسم التصنيف الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_new_cat")]]))
        return STATE_EDIT_NEW_CAT
    if data.startswith("edit_cat_"):
        cat_id = int(data.split('_')[-1]); slot = context.user_data.get('edit_taxonomy_slot', 1)
        fatwa = db.get_fatwa(fatwa_id); cls = fatwa.get('classifications', [])
        found = False
        for c in cls:
            if c['slot_index'] == slot: c['category_id'] = cat_id; c['topic_ids'] = []; found = True; break
        if not found: cls.append({'category_id': cat_id, 'topic_ids': [], 'slot_index': slot})
        db.update_fatwa(fatwa_id, {'classifications': cls}); context.user_data['edit_topic_cat_id'] = cat_id
        return await show_edit_topics_step(update, context, cat_id=cat_id)
    return STATE_EDIT_CATEGORY

async def show_edit_topics_step(update, context, cat_id=None, page=0, search_query=None):
    cat_row = db.get_category(cat_id); cat_name = cat_row['name'] if cat_row else "غير محدد"
    topics = db.get_topics_by_category(cat_id); slot = context.user_data.get('edit_taxonomy_slot', 1)
    fatwa = db.get_fatwa(context.user_data.get('edit_fatwa_id'))
    current_topics = []
    for cls in fatwa.get('classifications', []):
        if cls['slot_index'] == slot and cls['category_id'] == cat_id:
            current_topics = cls.get('topic_ids', []); break
    kb = []
    for tid, name in topics:
        kb.append(InlineKeyboardButton(f"✅ {name}" if tid in current_topics else name, callback_data=f"edit_toggle_top_{tid}"))
    keyboard = _pair_buttons(kb)
    keyboard.append([InlineKeyboardButton("📌 حفظ المواضيع", callback_data="edit_done_topics")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=f"edit_fatwa_{fatwa['id']}")])
    await update.callback_query.edit_message_text(f"🏷️ التصنيف: {cat_name}\n📑 عدل المواضيع:", reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_EDIT_TOPIC

async def handle_edit_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    fatwa_id = context.user_data.get('edit_fatwa_id'); slot = context.user_data.get('edit_taxonomy_slot', 1)
    cat_id = context.user_data.get('edit_topic_cat_id')
    if data.startswith("edit_toggle_top_"):
        topic_id = int(data.split('_')[-1]); fatwa = db.get_fatwa(fatwa_id); cls = fatwa.get('classifications', [])
        for c in cls:
            if c['slot_index'] == slot and c['category_id'] == cat_id:
                tids = c.setdefault('topic_ids', [])
                if topic_id in tids: tids.remove(topic_id)
                else: tids.append(topic_id)
                break
        db.update_fatwa(fatwa_id, {'classifications': cls}); return await show_edit_topics_step(update, context, cat_id=cat_id)
    if data == "edit_done_topics": return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
    return STATE_EDIT_TOPIC

async def handle_edit_cat_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip(); await show_edit_categories_step(update, context, search_query=query)
    return STATE_EDIT_CATEGORY

async def show_edit_categories_step(update, context, page=0, search_query=None):
    slot = context.user_data.get('edit_taxonomy_slot', 1); cat_type = "fiqh" if slot == 1 else "topic"
    cats = db.get_categories(search_query=search_query, category_type=cat_type)
    kb = []
    for cid, name in cats: kb.append(InlineKeyboardButton(name, callback_data=f"edit_cat_{cid}"))
    keyboard = _pair_buttons(kb)
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"edit_fatwa_{context.user_data.get('edit_fatwa_id')}")])
    msg = "🏷️ اختر التصنيف الجديد:"
    if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_receive_new_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip(); slot = context.user_data.get('edit_taxonomy_slot', 1)
    cat_type = "fiqh" if slot == 1 else "topic"; cat_id = db.add_category(name, category_type=cat_type)
    fatwa_id = context.user_data.get('edit_fatwa_id'); fatwa = db.get_fatwa(fatwa_id); cls = fatwa.get('classifications', [])
    cls.append({'category_id': cat_id, 'topic_ids': [], 'slot_index': slot})
    db.update_fatwa(fatwa_id, {'classifications': cls}); return await show_edit_topics_step(update, context, cat_id=cat_id)

add_fatwa_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_add_fatwa, pattern='^add_fatwa$')],
    states={
        STATE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
        STATE_SCHOLAR: [CallbackQueryHandler(handle_scholar_selection), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar)],
        STATE_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_question)],
        STATE_FATWA_TEXT: [CallbackQueryHandler(confirm_fatwa_text, pattern='^confirm_fatwa_text$'), CallbackQueryHandler(handle_duplicate_fatwa_choice, pattern='^dup_(cancel|continue)_add$'), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_fatwa_text)],
        STATE_CATEGORIES: [CallbackQueryHandler(handle_category_selection), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category)],
        STATE_TOPICS: [CallbackQueryHandler(handle_topic_selection), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_topic)],
        STATE_SOURCE: [CallbackQueryHandler(handle_source_selection, pattern='^(pick_source_|source_page_|source_manual$)'), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source)],
        STATE_SOURCE_TITLE: [CallbackQueryHandler(skip_source_title, pattern='^skip_source_title$'), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source_title)],
        STATE_SOURCE_URL: [CallbackQueryHandler(receive_source_url, pattern='^skip_source_url$'), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source_url)],
        STATE_AUDIO: [CallbackQueryHandler(receive_audio, pattern='^skip_audio$'), MessageHandler(filters.TEXT & ~filters.COMMAND, receive_audio)],
        STATE_ADD_FATWA_SCHOLAR_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_scholar_search_add), CallbackQueryHandler(handle_scholar_selection, pattern='^scholar_page_'), CallbackQueryHandler(handle_scholar_selection, pattern='^scholar_search_cancel$')],
        STATE_ADD_FATWA_CAT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category_search_add), CallbackQueryHandler(handle_category_selection, pattern='^cat_page_')],
        STATE_ADD_FATWA_TOPIC_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic_search_add), CallbackQueryHandler(handle_topic_selection, pattern='^topic_page_')]
    },
    fallbacks=[CallbackQueryHandler(handle_back_step, pattern='^back_step$'), CallbackQueryHandler(cancel_operation, pattern='^cancel$'), CommandHandler('cancel', cancel_operation)]
)

edit_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_edit_fatwa, pattern='^edit_fatwa_')],
    states={
        STATE_EDIT_MENU: [CallbackQueryHandler(handle_edit_menu)],
        STATE_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_value), CallbackQueryHandler(handle_edit_menu, pattern='^cancel_edit$')],
        STATE_EDIT_CATEGORY: [CallbackQueryHandler(handle_edit_category_selection)],
        STATE_EDIT_TOPIC: [CallbackQueryHandler(handle_edit_topic_selection)],
        STATE_EDIT_CAT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_cat_search)],
        STATE_EDIT_NEW_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_new_cat)]
    },
    fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel$'), CommandHandler('cancel', cancel_operation)]
)
