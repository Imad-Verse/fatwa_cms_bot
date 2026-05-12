import logging
from typing import List, Dict, Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.utils import escape_markdown, safe_reply_text, safe_edit_message_text
from .utils import (
    _ensure_admin, _parse_int_list_setting, _serialize_int_list_setting
)

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

TARGETED_TOPICS_SETTING_KEY = "auto_publish_topic_ids"
SCHEDULED_FATWA_SETTING_KEY = "auto_publish_scheduled_fatwa_number"
AWAITING_SCHEDULED_FATWA_INPUT_KEY = "awaiting_scheduled_fatwa_number"

async def _get_selected_publish_category() -> Tuple[Optional[int], Optional[Dict]]:
    raw_value = (await bot_db.get_setting('auto_publish_category_id', '') or '').strip()
    if not raw_value: return None, None
    try: cat_id = int(raw_value)
    except:
        await bot_db.set_setting('auto_publish_category_id', ''); await bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
        return None, None
    category = await db.get_category(cat_id)
    if not category:
        await bot_db.set_setting('auto_publish_category_id', ''); await bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
        return None, None
    return cat_id, category

async def _load_targeted_topic_selection(category_id: int) -> Tuple[List[int], Dict[int, str]]:
    topics, _ = await db.get_topics_by_category(category_id)
    topic_map = {int(t['id']): t['name'] for t in topics}
    raw_value = await bot_db.get_setting(TARGETED_TOPICS_SETTING_KEY, '') or ''
    selected_ids = _parse_int_list_setting(raw_value)
    norm_ids = [tid for tid in selected_ids if tid in topic_map]
    if norm_ids != selected_ids or raw_value != _serialize_int_list_setting(selected_ids):
        await bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, _serialize_int_list_setting(norm_ids))
    return norm_ids, topic_map

def _build_topic_selection_preview(selected_topic_ids: List[int], topic_map: Dict[int, str]) -> str:
    if not selected_topic_ids: return "كل مواضيع التصنيف"
    names = [topic_map.get(tid) for tid in selected_topic_ids if tid in topic_map]
    names = [n for n in names if n]
    if not names: return "كل مواضيع التصنيف"
    shown = ", ".join(escape_markdown(n) for n in names[:4])
    if len(names) > 4: shown += f" ... (+{len(names) - 4})"
    return f"{len(names)} موضوع: {shown}"

async def _clear_scheduled_fatwa() -> None: await bot_db.set_setting(SCHEDULED_FATWA_SETTING_KEY, '')

async def _get_scheduled_fatwa_number() -> Optional[int]:
    val = (await bot_db.get_setting(SCHEDULED_FATWA_SETTING_KEY, '') or '').strip()
    if not val: return None
    try:
        num = int(val)
        if num <= 0: await _clear_scheduled_fatwa(); return None
        return num
    except: await _clear_scheduled_fatwa(); return None

async def _get_scheduled_fatwa() -> Tuple[Optional[int], Optional[Dict]]:
    num = await _get_scheduled_fatwa_number()
    if not num: return None, None
    fatwa = await db.get_fatwa_by_number(num)
    if not fatwa or fatwa.get('status') != 'published': await _clear_scheduled_fatwa(); return None, None
    return num, fatwa

async def auto_publish_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة إدارة النشر التلقائي"""
    query = update.callback_query; await query.answer()
    if not await _ensure_admin(update, query): return
    context.user_data.pop('awaiting_pub_cat_search', None); context.user_data.pop(AWAITING_SCHEDULED_FATWA_INPUT_KEY, None)

    auto_publish = await bot_db.get_setting('auto_publish', '0'); specific_enabled = await bot_db.get_setting('auto_publish_specific', '0')
    scheduled_num, scheduled_fatwa = await _get_scheduled_fatwa()

    # تحديد النص الوصفي للحالة العامة
    if auto_publish == '1': base_status = "النشر العشوائي (فعّال)"
    elif specific_enabled == '1':
        _, category = await _get_selected_publish_category(); cat_name = category['name'] if category else "غير محدد"
        base_status = f"النشر المحدد: {escape_markdown(cat_name)} (فعّال)"
    else: base_status = "النشر التلقائي (معطّل)"

    # الحالة الرئيسية التي تظهر للمستخدم
    if scheduled_num and scheduled_fatwa:
        status_text = f"🗓️ **مجدول حالياً (فتوى #{scheduled_num})**"
        status_note = f"⚠️ سيتم نشر الفتوى المجدولة أولاً، ثم العودة إلى: **{base_status}**."
    else:
        status_text = f"✅ **{base_status}**" if (auto_publish == '1' or specific_enabled == '1') else f"❌ **{base_status}**"
        status_note = "💡 لا توجد فتاوى مجدولة حالياً."

    toggle_btn_text = "🔴 إيقاف النشر العشوائي" if auto_publish == '1' else "🟢 تفعيل النشر العشوائي"
    sch_btn_text = f"🗓️ تعديل الجدولة (#{scheduled_num})" if scheduled_num else "🗓️ جدولة فتوى محددة"
    
    sch_info = ""
    if scheduled_num and scheduled_fatwa:
        sch_info = f"📌 **الجدولة القادمة:** فتوى رقم `{scheduled_num}`\n📝 **العنوان:** {escape_markdown(scheduled_fatwa['title'])}\n⚠️ *سيتم نشر هذه الفتوى حصراً في الموعد القادم بدلاً من النشر العشوائي.*\n\n"
    else:
        sch_info = "🗓️ **الجدولة:** لا توجد فتوى محددة (سيتم استخدام النظام العشوائي).\n\n"

    keyboard = [
        [InlineKeyboardButton("🚀 نشر الآن (فوري)", callback_data="force_publish_now"), InlineKeyboardButton(sch_btn_text, callback_data="schedule_fatwa_once")],
        [InlineKeyboardButton(toggle_btn_text, callback_data="toggle_auto_publish_master")],
        [InlineKeyboardButton("🎯 إعدادات النشر المحدد (تصنيفات)", callback_data="targeted_publish_panel")],
        [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]
    ]
    
    message_text = (
        f"⚙️ **إدارة النشر التلقائي**\n\n"
        f"📊 **الحالة:** {status_text}\n"
        f"ℹ️ {status_note}\n\n"
        f"{sch_info}"
        f"💡 يمكنك جدولة فتوى معينة للنشر القادم، أو ترك البوت يختار عشوائياً وفق الإعدادات."
    )
    
    await safe_edit_message_text(query, message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def clear_scheduled_fatwa_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مسح الجدولة الحالية"""
    query = update.callback_query; await query.answer()
    if not await _ensure_admin(update, query): return
    await _clear_scheduled_fatwa()
    await query.answer("🗑️ تم إلغاء الجدولة", show_alert=True)
    await auto_publish_panel(update, context)

async def toggle_auto_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل النشر التلقائي (Global/Random)"""
    query = update.callback_query; await query.answer()
    if not await _ensure_admin(update, query): return
    current = await bot_db.get_setting('auto_publish', '0')
    if current == '0':
        # تفعيل النشر العشوائي يعطل النشر المحدد ويمسح الجدولة
        await bot_db.set_setting('auto_publish', '1')
        await bot_db.set_setting('auto_publish_specific', '0')
        await _clear_scheduled_fatwa()
    else:
        await bot_db.set_setting('auto_publish', '0')
    await auto_publish_panel(update, context)

async def start_schedule_fatwa_once(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب رقم فتوى ليتم نشرها مرة واحدة."""
    query = update.callback_query; await query.answer()
    if not await _ensure_admin(update, query): return
    context.user_data[AWAITING_SCHEDULED_FATWA_INPUT_KEY] = True
    context.user_data.pop('awaiting_pub_cat_search', None)
    num, _ = await _get_scheduled_fatwa(); prompt = f"🗓️ **جدولة فتوى**\n\nأرسل رقم الفتوى للنشر القادم."
    keyboard = []
    if num:
        prompt += f"\n\n📌 الجدولة الحالية: فتوى رقم `{num}`\n💡 أرسل رقمًا جديدًا للاستبدال، أو اضغط إلغاء الجدولة أدناه."
        keyboard.append([InlineKeyboardButton("🗑️ إلغاء الجدولة الحالية", callback_data="clear_scheduled_fatwa")])
    
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="auto_publish_panel")])
    await safe_edit_message_text(query, prompt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def targeted_publish_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة النشر المحدد"""
    query = update.callback_query; await query.answer()
    if not await _ensure_admin(update, query): return
    specific_enabled = await bot_db.get_setting('auto_publish_specific', '0') == '1'; cat_id, category = await _get_selected_publish_category()
    if specific_enabled and not cat_id: await bot_db.set_setting('auto_publish_specific', '0'); specific_enabled = False
    status_icon, toggle_label = ("✅ مفعل", "إيقاف ❌") if specific_enabled else ("❌ معطل", "تفعيل ✅")
    cat_name, topic_ids, topic_preview = (category['name'], [], "اختر تصنيفًا أولاً") if category else ("غير محدد", [], "اختر تصنيفًا أولاً")
    if cat_id: topic_ids, topic_map = await _load_targeted_topic_selection(cat_id); topic_preview = _build_topic_selection_preview(topic_ids, topic_map)
    keyboard = [[InlineKeyboardButton(f"📂 اختيار تصنيف ({cat_name})", callback_data="sel_pub_cat_start")]]
    if cat_id: keyboard.append([InlineKeyboardButton(f"🧩 اختيار المواضيع ({len(topic_ids) if topic_ids else 'الكل'})", callback_data="sel_pub_top_start")])
    if topic_ids: keyboard.append([InlineKeyboardButton("🧹 مسح اختيار المواضيع", callback_data="clear_pub_topics")])
    keyboard.extend([[InlineKeyboardButton(toggle_label, callback_data="toggle_targeted_publish")], [InlineKeyboardButton("🔙 رجوع", callback_data="auto_publish_panel")]])
    text = f"🎯 **النشر المحدد**\n\nالحالة: {status_icon}\nالتصنيف: **{escape_markdown(cat_name)}**\nالمواضيع: {topic_preview}"
    await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def toggle_targeted_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تفعيل/تعطيل النشر المحدد"""
    query = update.callback_query
    if not await _ensure_admin(update, query): return
    current = await bot_db.get_setting('auto_publish_specific', '0')
    if current == '0':
        cat_id, _ = await _get_selected_publish_category()
        if not cat_id:
            await query.answer("⚠️ يرجى اختيار تصنيف أولاً!", show_alert=True)
            return
        # تفعيل النشر المحدد يعطل النشر العشوائي
        await bot_db.set_setting('auto_publish_specific', '1')
        await bot_db.set_setting('auto_publish', '0')
    else:
        await bot_db.set_setting('auto_publish_specific', '0')
    await query.answer()
    await targeted_publish_panel(update, context)

async def start_select_publish_category(update: Update, context: ContextTypes.DEFAULT_TYPE, search_query=None):
    """عرض قائمة التصنيفات للاختيار"""
    query = update.callback_query
    if query:
        await query.answer()
        if not await _ensure_admin(update, query): return
    else:
        if not await _ensure_admin(update): return
    context.user_data.pop('awaiting_pub_cat_search', None)
    data = query.data if query else ""
    page = int(data.split("sel_pub_cat_page_")[-1]) if "sel_pub_cat_page_" in data else 0
    if search_query is None and query: search_query = context.user_data.get('pub_cat_search_query')
    ITEMS, offset = 8, page * 8
    categories = await db.get_categories(limit=ITEMS, offset=offset, search_query=search_query)
    total = await db.get_categories_count(search_query=search_query)
    keyboard = []
    for i in range(0, len(categories), 2):
        keyboard.append([InlineKeyboardButton(name, callback_data=f"set_pub_cat_{cid}") for cid, name in categories[i:i+2]])
    # Navigation Row
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sel_pub_cat_page_{page-1}"))
    if offset + ITEMS < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sel_pub_cat_page_{page+1}"))
    
    if nav_row:
        # keyboard.insert(0, nav_row) # Top - REMOVED
        keyboard.append(nav_row)    # Bottom

    keyboard.append([InlineKeyboardButton("🔍 بحث عن تصنيف", callback_data="search_pub_cat")])
    if search_query: keyboard.append([InlineKeyboardButton("🔙 إلغاء البحث", callback_data="clear_pub_cat_search")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="targeted_publish_panel")])
    title = f"📂 **اختيار التصنيف**\n" + (f"🔍 نتائج البحث: {escape_markdown(search_query)}" if search_query else "اختر التصنيف للنشر:")
    await safe_reply_text(update, title, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def start_search_publish_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء وضع البحث"""
    query = update.callback_query
    await query.answer()
    if not await _ensure_admin(update, query): return
    context.user_data['awaiting_pub_cat_search'] = True
    context.user_data.pop(AWAITING_SCHEDULED_FATWA_INPUT_KEY, None)
    await safe_edit_message_text(query, "🔍 **بحث عن تصنيف**\n\nأرسل اسم التصنيف:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="sel_pub_cat_start")]]))

async def clear_publish_category_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء البحث"""
    if not await _ensure_admin(update, update.callback_query): return
    context.user_data.pop('pub_cat_search_query', None)
    context.user_data.pop('awaiting_pub_cat_search', None)
    await start_select_publish_category(update, context)

async def handle_publish_category_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال مدخلات النص"""
    if not await _ensure_admin(update): return
    if context.user_data.get(AWAITING_SCHEDULED_FATWA_INPUT_KEY):
        try:
            num = int(update.message.text.strip())
            fatwa = await db.get_fatwa_by_number(num)
            if not fatwa:
                await update.message.reply_text(f"❌ لم يتم العثور على فتوى رقم: {num}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]]))
                return
            if fatwa.get('status') != 'published':
                await update.message.reply_text("⚠️ لا يمكن جدولة غير منشورة.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]]))
                return
            await bot_db.set_setting(SCHEDULED_FATWA_SETTING_KEY, str(num))
            context.user_data.pop(AWAITING_SCHEDULED_FATWA_INPUT_KEY, None)
            
            await update.message.reply_text(
                f"✅ **تمت جدولة الفتوى بنجاح!**\n\n"
                f"🔢 رقم الفتوى: `{num}`\n"
                f"📝 العنوان: {escape_markdown(fatwa['title'])}\n\n"
                f"⏳ سيتم نشرها تلقائياً في الموعد القادم.",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ العودة للإعدادات", callback_data="auto_publish_panel")]])
            )
        except:
            await update.message.reply_text("⚠️ أرسل رقم فتوى صحيح.")
            return
        return
    if not context.user_data.get('awaiting_pub_cat_search'): return
    q = update.message.text.strip()
    context.user_data['pub_cat_search_query'] = q
    context.user_data['awaiting_pub_cat_search'] = False
    await start_select_publish_category(update, context, search_query=q)

async def set_publish_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حفظ التصنيف المختار"""
    query = update.callback_query
    if not await _ensure_admin(update, query): return
    cat_id = int(query.data.split('set_pub_cat_')[-1])
    category = await db.get_category(cat_id)
    if not category:
        await query.answer("⚠️ التصنيف غير موجود", show_alert=True)
        return
    prev_cat_id, _ = await _get_selected_publish_category()
    await bot_db.set_setting('auto_publish_category_id', str(cat_id))
    if prev_cat_id != cat_id:
        await bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
    context.user_data.pop('pub_cat_search_query', None)
    context.user_data.pop('awaiting_pub_cat_search', None)
    await query.answer("✅ تم اختيار التصنيف")
    await start_select_publish_topics(update, context, page=0)

async def start_select_publish_topics(update: Update, context: ContextTypes.DEFAULT_TYPE, page: Optional[int] = None):
    """عرض مواضيع التصنيف المختار"""
    query = update.callback_query
    if query:
        await query.answer()
        if not await _ensure_admin(update, query): return
    else:
        if not await _ensure_admin(update): return
    cat_id, category = await _get_selected_publish_category()
    if not cat_id or not category:
        if query:
            await query.answer("⚠️ يرجى اختيار تصنيف أولاً", show_alert=True)
            await targeted_publish_panel(update, context)
        return
    if page is None:
        page = int(query.data.split("sel_pub_top_page_")[-1]) if query and query.data.startswith("sel_pub_top_page_") else 0
    page = max(page, 0)
    selected_ids, topic_map = await _load_targeted_topic_selection(cat_id)
    ITEMS, offset = 8, page * 8
    topics, total = await db.get_topics_by_category(cat_id, limit=ITEMS, offset=offset)
    keyboard = []
    for i in range(0, len(topics), 2):
        row = []
        for t in topics[i:i+2]:
            tid, n = t['id'], t['name']
            row.append(InlineKeyboardButton(f"✅ {n}" if tid in selected_ids else n, callback_data=f"toggle_pub_top_{tid}_{page}"))
        keyboard.append(row)
    # Navigation Row
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sel_pub_top_page_{page-1}"))
    if offset + ITEMS < total: nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sel_pub_top_page_{page+1}"))
    
    if nav_row:
        # keyboard.insert(0, nav_row) # Top - REMOVED
        keyboard.append(nav_row)    # Bottom

    if selected_ids: keyboard.append([InlineKeyboardButton("🧹 مسح الاختيار", callback_data="clear_pub_topics")])
    keyboard.extend([[InlineKeyboardButton("✅ تم", callback_data="targeted_publish_panel")], [InlineKeyboardButton("🔙 رجوع", callback_data="targeted_publish_panel")]])
    preview = _build_topic_selection_preview(selected_ids, topic_map) if total > 0 else "لا توجد مواضيع"
    text = f"🧩 **اختيار المواضيع**\n\nالتصنيف: **{escape_markdown(category['name'])}**\nالمحدد: {preview}"
    await safe_reply_text(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def toggle_publish_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل اختيار موضوع"""
    query = update.callback_query
    if not await _ensure_admin(update, query): return
    parts = query.data.split("_")
    tid, page = int(parts[3]), int(parts[4]) if len(parts) > 4 else 0
    cat_id, _ = await _get_selected_publish_category()
    if not cat_id:
        await query.answer("⚠️ اختر تصنيف أولاً", show_alert=True)
        await targeted_publish_panel(update, context)
        return
    selected_ids, topic_map = await _load_targeted_topic_selection(cat_id)
    if tid not in topic_map:
        await query.answer("⚠️ الموضوع غير موجود", show_alert=True)
        return
    if tid in selected_ids: selected_ids.remove(tid)
    else: selected_ids.append(tid)
    await bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, _serialize_int_list_setting(selected_ids))
    await start_select_publish_topics(update, context, page=page)

async def clear_publish_topics_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مسح المواضيع المختارة"""
    query = update.callback_query
    if not await _ensure_admin(update, query): return
    await bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
    await start_select_publish_topics(update, context, page=0)
