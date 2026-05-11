import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import BotState
from handlers.general import cancel_operation

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

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
    fatwa = await db.get_fatwa(fatwa_id) if fatwa_id else None
    if not fatwa: return
    ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE
    scholars = await db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset); total = await db.get_scholars_count()
    current = str(fatwa.get('scholar_name') or "").strip()
    kb = []
    for sid, name in scholars: kb.append(InlineKeyboardButton(f"✅ {name}" if name == current else name, callback_data=f"edit_pick_scholar_{sid}"))
    keyboard = _pair_buttons(kb)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"edit_sch_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"edit_sch_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom

    keyboard.append([InlineKeyboardButton("➕ إضافة عالم جديد", callback_data="edit_add_new_scholar")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=f"edit_fatwa_{fatwa_id}")])
    text = f"👤 تعديل العالم\n\nالعالم الحالي: {current}\n\nاختر من القائمة:"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return BotState.STATE_EDIT_MENU

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
    if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    return BotState.STATE_EDIT_MENU

async def handle_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    fatwa_id = context.user_data.get('edit_fatwa_id')

    if data == "cancel_edit":
        from .view import view_fatwa
        await view_fatwa(update, context, fatwa_id=fatwa_id); return ConversationHandler.END
    if data.startswith("edit_fatwa_"): return await start_edit_fatwa(update, context, fatwa_id=int(data.split('_')[-1]))
    if data.startswith("edit_sch_page_"): return await _show_edit_scholar_picker(update, context, page=int(data.split('_')[-1]))
    if data.startswith("edit_pick_scholar_"):
        sid = int(data.split('_')[-1]); sch = await db.get_scholar_by_id(sid)
        if sch: await db.update_fatwa(fatwa_id, {'scholar_name': sch['name']})
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
    if data == "edit_add_new_scholar":
        context.user_data['edit_field'] = "scholar_name"
        text, markup = _build_edit_field_prompt(await db.get_fatwa(fatwa_id), "scholar_name", f"edit_fatwa_{fatwa_id}")
        await query.edit_message_text(text, reply_markup=markup); return BotState.STATE_EDIT_VALUE

    if data.startswith("edit_slot_"):
        slot = int(data.split('_')[-1]); context.user_data['edit_taxonomy_slot'] = slot
        summary = _format_edit_slot_summary(await db.get_fatwa(fatwa_id), slot)
        actions = [InlineKeyboardButton("تغيير التصنيف الحالي", callback_data=f"edit_tax_cat_{slot}"), InlineKeyboardButton("تعديل المواضيع", callback_data=f"edit_tax_top_{slot}"), InlineKeyboardButton("➕ إضافة تصنيف آخر", callback_data=f"add_another_cat_{slot}"), InlineKeyboardButton("🗑️ حذف كافة التصنيفات", callback_data="delete_all_fatwa_classifications")]
        if slot == 2: actions.append(InlineKeyboardButton("🗑️ حذف هذا النوع بالكامل", callback_data="delete_slot_2"))
        kb = _pair_buttons(actions); kb.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"edit_fatwa_{fatwa_id}")])
        await query.edit_message_text(f"🏷️ تعديل النوع {slot}\n\nالتصنيفات الحالية:\n{summary}\n\nاختر الإجراء:", reply_markup=InlineKeyboardMarkup(kb)); return BotState.STATE_EDIT_MENU

    if data.startswith("edit_tax_cat_") or data.startswith("add_another_cat_"):
        slot = int(data.split('_')[-1]); context.user_data['edit_taxonomy_slot'] = slot
        context.user_data['edit_append_mode'] = data.startswith("add_another_cat_")
        await show_edit_categories_step(update, context); return BotState.STATE_EDIT_CATEGORY

    if data.startswith("edit_tax_top_"):
        slot = int(data.split('_')[-1]); context.user_data['edit_taxonomy_slot'] = slot; fatwa = await db.get_fatwa(fatwa_id)
        cls_list = [c for c in fatwa.get('classifications', []) if c.get('slot_index') == slot]
        if not cls_list: await query.answer("⚠️ لا يوجد تصنيف لهذا النوع. اختر تصنيفاً أولاً.", show_alert=True); return BotState.STATE_EDIT_MENU
        cat_id = cls_list[0]['category_id']; context.user_data['edit_topic_cat_id'] = cat_id
        await show_edit_topics_step(update, context, cat_id=cat_id); return BotState.STATE_EDIT_TOPIC

    if data == "delete_all_fatwa_classifications":
        await db.update_fatwa(fatwa_id, {'classifications': []}); await query.answer("🗑️ تم حذف كافة التصنيفات")
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    if data == "delete_slot_2":
        fatwa = await db.get_fatwa(fatwa_id); cls = [c for c in fatwa.get('classifications', []) if c.get('slot_index') != 2]
        await db.update_fatwa(fatwa_id, {'classifications': cls}); await query.answer("🗑️ تم حذف التصنيف الموضوعي")
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    field_map = {"edit_field_title": "title", "edit_field_scholar": "scholar_name", "edit_field_question": "question", "edit_field_text": "answer", "edit_field_source_name": "source_name", "edit_field_source_title": "source_title", "edit_field_source_url": "source_url", "edit_field_audio": "audio_url"}
    if data in field_map:
        f = field_map[data]; context.user_data['edit_field'] = f
        text, markup = _build_edit_field_prompt(await db.get_fatwa(fatwa_id), f, f"edit_fatwa_{fatwa_id}")
        await query.edit_message_text(text, reply_markup=markup); return BotState.STATE_EDIT_VALUE
    return BotState.STATE_EDIT_MENU

async def receive_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = context.user_data.get('edit_field'); fatwa_id = context.user_data.get('edit_fatwa_id')
    await db.update_fatwa(fatwa_id, {field: update.message.text})
    await update.message.reply_text("✅ تم التحديث."); return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

async def handle_edit_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    fatwa_id = context.user_data.get('edit_fatwa_id'); slot = context.user_data.get('edit_taxonomy_slot', 1)

    if data == "search_edit_cat":
        await query.edit_message_text("🔍 أرسل اسم التصنيف للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="edit_cat_search_cancel")]]))
        return BotState.STATE_EDIT_CAT_SEARCH
    if data == "edit_cat_search_cancel": context.user_data.pop(f'edit_cat_search_{slot}', None); await show_edit_categories_step(update, context, page=0); return BotState.STATE_EDIT_CATEGORY
    if data == "add_new_edit_cat": await query.edit_message_text("🏷️ أرسل اسم التصنيف الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_new_cat")]])); return BotState.STATE_EDIT_NEW_CAT
    if data == "cancel_new_cat": await show_edit_categories_step(update, context, page=0); return BotState.STATE_EDIT_CATEGORY
    if data.startswith("edit_cat_page_"): await show_edit_categories_step(update, context, page=int(data.split('_')[-1])); return BotState.STATE_EDIT_CATEGORY
    if data.startswith("edit_cat_"):
        cat_id = int(data.split('_')[-1]); fatwa = await db.get_fatwa(fatwa_id); cls = fatwa.get('classifications', [])
        if not context.user_data.get('edit_append_mode', False): cls = [c for c in cls if c['slot_index'] != slot]
        if not any(c['category_id'] == cat_id and c['slot_index'] == slot for c in cls): cls.append({'category_id': cat_id, 'topic_ids': [], 'slot_index': slot})
        await db.update_fatwa(fatwa_id, {'classifications': cls}); context.user_data['edit_topic_cat_id'] = cat_id
        return await show_edit_topics_step(update, context, cat_id=cat_id)
    return STATE_EDIT_CATEGORY

async def show_edit_topics_step(update, context, cat_id=None, page=0, search_query=None):
    ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE; cat_row = await db.get_category(cat_id)
    cat_name = cat_row['name'] if cat_row else "غير محدد"
    if search_query is None: search_query = context.user_data.get(f'edit_topic_search_{cat_id}')
    else: context.user_data[f'edit_topic_search_{cat_id}'] = search_query
    topics = await db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = await db.get_topics_count(cat_id, search_query=search_query); slot = context.user_data.get('edit_taxonomy_slot', 1); fatwa_id = context.user_data.get('edit_fatwa_id')
    fatwa = await db.get_fatwa(fatwa_id); current_topics = []
    for cls in fatwa.get('classifications', []):
        if cls['slot_index'] == slot and cls['category_id'] == cat_id: current_topics = cls.get('topic_ids', []); break
    kb = []
    for tid, name in topics: kb.append(InlineKeyboardButton(f"✅ {name}" if tid in current_topics else name, callback_data=f"edit_toggle_top_{tid}"))
    keyboard = _pair_buttons(kb)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"edit_top_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"edit_top_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom
    keyboard.append([InlineKeyboardButton("🔍 بحث موضوع", callback_data="search_edit_topic"), InlineKeyboardButton("➕ موضوع جديد", callback_data="add_new_edit_topic")])
    keyboard.append([InlineKeyboardButton("📌 حفظ المواضيع", callback_data="edit_done_topics")]); keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=f"edit_fatwa_{fatwa_id}")])
    msg = f"🏷️ التصنيف: {cat_name}\n📑 عدل المواضيع:"
    if update.callback_query: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return BotState.STATE_EDIT_TOPIC

async def handle_edit_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    fatwa_id = context.user_data.get('edit_fatwa_id'); slot = context.user_data.get('edit_taxonomy_slot', 1); cat_id = context.user_data.get('edit_topic_cat_id')
    if data == "search_edit_topic":
        await query.edit_message_text("🔍 أرسل اسم الموضوع للبحث عنه:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء البحث", callback_data="edit_topic_search_cancel")]]))
        return BotState.STATE_EDIT_TOP_SEARCH
    if data == "edit_topic_search_cancel": context.user_data.pop(f'edit_topic_search_{cat_id}', None); await show_edit_topics_step(update, context, cat_id=cat_id, page=0); return BotState.STATE_EDIT_TOPIC
    if data == "add_new_edit_topic": await query.edit_message_text("📑 أرسل اسم الموضوع الجديد:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_new_topic")]])); return BotState.STATE_EDIT_NEW_TOP
    if data == "cancel_new_topic": await show_edit_topics_step(update, context, cat_id=cat_id, page=0); return BotState.STATE_EDIT_TOPIC
    if data.startswith("edit_top_page_"): await show_edit_topics_step(update, context, cat_id=cat_id, page=int(data.split('_')[-1])); return BotState.STATE_EDIT_TOPIC
    if data.startswith("edit_toggle_top_"):
        tid = int(data.split('_')[-1]); fatwa = await db.get_fatwa(fatwa_id); cls = fatwa.get('classifications', [])
        for c in cls:
            if c['slot_index'] == slot and c['category_id'] == cat_id:
                tids = c.setdefault('topic_ids', []); 
                if tid in tids: tids.remove(tid)
                else: tids.append(tid)
                break
        await db.update_fatwa(fatwa_id, {'classifications': cls}); return await show_edit_topics_step(update, context, cat_id=cat_id)
    if data == "edit_done_topics": return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
    return BotState.STATE_EDIT_TOPIC

async def handle_edit_cat_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip(); slot = context.user_data.get('edit_taxonomy_slot', 1); context.user_data[f'edit_cat_search_{slot}'] = q
    await show_edit_categories_step(update, context, search_query=q); return BotState.STATE_EDIT_CATEGORY

async def handle_edit_topic_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.message.text.strip(); cid = context.user_data.get('edit_topic_cat_id'); context.user_data[f'edit_topic_search_{cid}'] = q
    await show_edit_topics_step(update, context, cat_id=cid, search_query=q); return BotState.STATE_EDIT_TOPIC

async def show_edit_categories_step(update, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 8; offset = page * ITEMS_PER_PAGE; slot = context.user_data.get('edit_taxonomy_slot', 1); cat_type = "fiqh" if slot == 1 else "topic"
    if search_query is None: search_query = context.user_data.get(f'edit_cat_search_{slot}')
    else: context.user_data[f'edit_cat_search_{slot}'] = search_query
    cats = await db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type)
    total = await db.get_categories_count(search_query=search_query, category_type=cat_type)
    kb = []
    for cid, name in cats: kb.append(InlineKeyboardButton(name, callback_data=f"edit_cat_{cid}"))
    keyboard = _pair_buttons(kb)
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"edit_cat_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"edit_cat_page_{page+1}"))
    
    if nav_row:
        keyboard.insert(0, nav_row) # Top
        keyboard.append(nav_row)    # Bottom
    keyboard.append([InlineKeyboardButton("🔍 بحث تصنيف", callback_data="search_edit_cat"), InlineKeyboardButton("➕ تصنيف جديد", callback_data="add_new_edit_cat")]); keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"edit_fatwa_{context.user_data.get('edit_fatwa_id')}")])
    if update.callback_query: await update.callback_query.edit_message_text("🏷️ اختر التصنيف الجديد:", reply_markup=InlineKeyboardMarkup(keyboard))
    else: await update.message.reply_text("🏷️ اختر التصنيف الجديد:", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_receive_new_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip(); slot = context.user_data.get('edit_taxonomy_slot', 1); cat_type = "fiqh" if slot == 1 else "topic"
    cid = await db.add_category(name, category_type=cat_type); fid = context.user_data.get('edit_fatwa_id'); fatwa = await db.get_fatwa(fid); cls = fatwa.get('classifications', [])
    if not context.user_data.get('edit_append_mode', False): cls = [c for c in cls if c['slot_index'] != slot]
    cls.append({'category_id': cid, 'topic_ids': [], 'slot_index': slot}); await db.update_fatwa(fid, {'classifications': cls}); context.user_data['edit_topic_cat_id'] = cid
    return await show_edit_topics_step(update, context, cat_id=cid)

async def handle_receive_new_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip(); cid = context.user_data.get('edit_topic_cat_id')
    if not cid: return BotState.STATE_EDIT_TOPIC
    tid = await db.add_topic(name, cid)
    if tid:
        fid = context.user_data.get('edit_fatwa_id'); slot = context.user_data.get('edit_taxonomy_slot', 1); fatwa = await db.get_fatwa(fid); cls = fatwa.get('classifications', [])
        for c in cls:
            if c['slot_index'] == slot and c['category_id'] == cid:
                tids = c.setdefault('topic_ids', [])
                if tid not in tids: tids.append(tid)
                break
        await db.update_fatwa(fid, {'classifications': cls})
    return await show_edit_topics_step(update, context, cat_id=cid)

edit_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_edit_fatwa, pattern='^edit_fatwa_')],
    states={
        BotState.STATE_EDIT_MENU: [CallbackQueryHandler(handle_edit_menu)],
        BotState.STATE_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_value), CallbackQueryHandler(handle_edit_menu, pattern='^cancel_edit$')],
        BotState.STATE_EDIT_CATEGORY: [CallbackQueryHandler(handle_edit_category_selection)],
        BotState.STATE_EDIT_TOPIC: [CallbackQueryHandler(handle_edit_topic_selection)],
        BotState.STATE_EDIT_CAT_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_cat_search)],
        BotState.STATE_EDIT_NEW_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_new_cat)],
        BotState.STATE_EDIT_TOP_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_topic_search)],
        BotState.STATE_EDIT_NEW_TOP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_new_topic)]
    },
    fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel$'), CommandHandler('cancel', cancel_operation)],
    persistent=False,
    name="edit_fatwa_conv"
)
