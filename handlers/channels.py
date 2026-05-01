import logging
import html

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember

from telegram.ext import ContextTypes

from telegram.error import BadRequest


from core.database import FatwaDatabaseManager

from core.bot_db import BotDatabaseManager
from core.keyboards import create_published_fatwa_keyboard

from core.config import OWNER_ID

from core.utils import build_fatwa_preview_text, escape_markdown, split_long_message, safe_send_message, notify_new_subscription


logger = logging.getLogger(__name__)

db = FatwaDatabaseManager()

bot_db = BotDatabaseManager()

_DELIVERY_LOG_KEY = "fatwa_delivery_log"


TARGETED_TOPICS_SETTING_KEY = "auto_publish_topic_ids"
SCHEDULED_FATWA_SETTING_KEY = "auto_publish_scheduled_fatwa_number"
AWAITING_SCHEDULED_FATWA_INPUT_KEY = "awaiting_scheduled_fatwa_number"


def _build_delivery_report_keyboard(fatwa_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ الحذف من الكل", callback_data=f"del_all_fatwa_{fatwa_id}")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
    ])


def _register_delivery_message(
    context: ContextTypes.DEFAULT_TYPE,
    fatwa_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    app = getattr(context, "application", None)
    if not app:
        return

    store = app.bot_data.setdefault(_DELIVERY_LOG_KEY, {})
    fatwa_store = store.setdefault(str(int(fatwa_id)), {})
    chat_key = str(int(chat_id))
    msg_list = fatwa_store.setdefault(chat_key, [])

    msg_id = int(message_id)
    if msg_id not in msg_list:
        msg_list.append(msg_id)
        if len(msg_list) > 200:
            del msg_list[:-200]


async def _ensure_admin(update: Update, query=None) -> bool:
    user = update.effective_user
    is_admin = bool(user and bot_db.is_admin(user.id))
    if is_admin:
        return True
    if query:
        try:
            await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        except Exception:
            if query.message:
                await query.message.reply_text("❌ هذا القسم للمسؤولين فقط")
    elif update.message:
        await update.message.reply_text("❌ هذا القسم للمسؤولين فقط")
    return False


async def _safe_edit_message_text(query, text: str, **kwargs):

    """Edit message text and ignore 'Message is not modified' errors."""

    try:

        await query.edit_message_text(text, **kwargs)

    except BadRequest as e:

        if "Message is not modified" in str(e):

            return

        raise


def _parse_int_list_setting(raw_value: str) -> List[int]:

    if not raw_value:
        return []

    values: List[int] = []
    seen = set()

    for part in raw_value.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        values.append(value)

    return values


def _serialize_int_list_setting(values: List[int]) -> str:

    cleaned: List[int] = []
    seen = set()

    for value in values:
        try:
            int_value = int(value)
        except (TypeError, ValueError):
            continue
        if int_value <= 0 or int_value in seen:
            continue
        seen.add(int_value)
        cleaned.append(int_value)

    return ",".join(str(v) for v in cleaned)


def _get_selected_publish_category() -> Tuple[Optional[int], Optional[Dict]]:

    raw_value = (bot_db.get_setting('auto_publish_category_id', '') or '').strip()
    if not raw_value:
        return None, None

    try:
        cat_id = int(raw_value)
    except (TypeError, ValueError):
        bot_db.set_setting('auto_publish_category_id', '')
        bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
        return None, None

    category = db.get_category(cat_id)
    if not category:
        bot_db.set_setting('auto_publish_category_id', '')
        bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
        return None, None

    return cat_id, category


def _load_targeted_topic_selection(category_id: int) -> Tuple[List[int], Dict[int, str]]:

    topics = db.get_topics_by_category(category_id)
    topic_map = {int(topic_id): name for topic_id, name in topics}

    raw_value = bot_db.get_setting(TARGETED_TOPICS_SETTING_KEY, '') or ''
    selected_topic_ids = _parse_int_list_setting(raw_value)
    normalized_topic_ids = [topic_id for topic_id in selected_topic_ids if topic_id in topic_map]

    if normalized_topic_ids != selected_topic_ids or raw_value != _serialize_int_list_setting(selected_topic_ids):
        bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, _serialize_int_list_setting(normalized_topic_ids))

    return normalized_topic_ids, topic_map


def _build_topic_selection_preview(selected_topic_ids: List[int], topic_map: Dict[int, str]) -> str:

    if not selected_topic_ids:
        return "كل مواضيع التصنيف"

    names = [topic_map.get(topic_id) for topic_id in selected_topic_ids if topic_id in topic_map]
    names = [name for name in names if name]

    if not names:
        return "كل مواضيع التصنيف"

    shown = ", ".join(escape_markdown(name) for name in names[:4])
    if len(names) > 4:
        shown += f" ... (+{len(names) - 4})"

    return f"{len(names)} موضوع: {shown}"


def _clear_scheduled_fatwa() -> None:

    bot_db.set_setting(SCHEDULED_FATWA_SETTING_KEY, '')


def _get_scheduled_fatwa_number() -> Optional[int]:

    raw_value = (bot_db.get_setting(SCHEDULED_FATWA_SETTING_KEY, '') or '').strip()
    if not raw_value:
        return None

    try:
        fatwa_number = int(raw_value)
    except (TypeError, ValueError):
        _clear_scheduled_fatwa()
        return None

    if fatwa_number <= 0:
        _clear_scheduled_fatwa()
        return None

    return fatwa_number


def _get_scheduled_fatwa() -> Tuple[Optional[int], Optional[Dict]]:

    fatwa_number = _get_scheduled_fatwa_number()
    if not fatwa_number:
        return None, None

    fatwa = db.get_fatwa_by_number(fatwa_number)
    if not fatwa or fatwa.get('status') != 'published':
        _clear_scheduled_fatwa()
        return None, None

    return fatwa_number, fatwa


def _normalize_chat_type(chat_type: str | None) -> str:

    if chat_type in ("group", "supergroup"):

        return "group"

    return "channel"


def _has_manage_messages_permission(chat_type: str, member: ChatMember) -> bool:

    status = getattr(member, "status", None)

    if status in (ChatMember.OWNER, ChatMember.ADMINISTRATOR):

        if chat_type == "channel":

            # For channels, require post permission when available.

            if hasattr(member, "can_post_messages"):

                return bool(member.can_post_messages)

            return True

        # For groups/supergroups, "manage messages" maps to delete permission.

        if hasattr(member, "can_delete_messages"):

            return bool(member.can_delete_messages)

        return True

    return False


async def _check_manage_messages_permission(context: ContextTypes.DEFAULT_TYPE, chat_id: int, chat_type: str) -> bool:

    try:

        member = await context.bot.get_chat_member(chat_id, context.bot.id)

    except Exception as e:
        logger.debug(f"get_chat_member failed for chat_id={chat_id}: {e}")
        return False

    return _has_manage_messages_permission(chat_type, member)


def _is_bot_removed_chat_error(error: Exception) -> bool:
    err = str(error).lower()
    removal_tokens = (
        "chat not found",
        "bot was kicked",
        "bot is not a member",
        "user not found",
    )
    return any(token in err for token in removal_tokens)


# ==================== Chat Member Updates (Auto-Detection) ====================


async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """تتبع تحديثات حالة البوت في القنوات والمجموعات"""

    try:

        result = update.my_chat_member

        if not result:

            return


        chat = result.chat

        new_status = result.new_chat_member.status


        # تحديد النوع

        chat_type = 'group'

        if chat.type == 'channel':

            chat_type = 'channel'

        elif chat.type in ['group', 'supergroup']:

            chat_type = 'group'

        else:

            return # Private or other


        # هل البوت أصبح أدمن أو عضو؟

        # في القنوات، يجب أن يكون أدمن للنشر. في المجموعات، يكفي عضو ولكن يفضل أدمن.

        # سنضيفه كـ active إذا كان (administrator, creator, member)

        # ونحذفه (inactive) إذا كان (kicked, left, restricted)


        has_manage_perm = _has_manage_messages_permission(chat_type, result.new_chat_member)


        # بالنسبة للقنوات، البوت لا يمكنه النشر إلا إذا كان أدمن


        status_str = 'active' if has_manage_perm else 'inactive'

        if status_str == 'active':

            logger.info(f"Bot active in {chat_type}: {chat.title} ({chat.id})")

        else:

            logger.info(f"Bot inactive or missing permissions in {chat_type}: {chat.title} ({chat.id})")


        # Upsert status to keep list accurate (active vs inactive).

        # Check if new
        is_new = not bot_db.channel_exists(chat.id)
        
        bot_db.add_channel(chat.id, chat.title, chat.username, chat_type, status_str)

        if is_new and status_str == 'active':
             await notify_new_subscription(
                context.bot,
                chat_type,
                {'id': chat.id, 'name': chat.title, 'username': chat.username},
                context
            )


    except Exception as e:

        logger.error(f"Error in track_chat_member: {e}", exc_info=True)


# ==================== Admin Panel: Manage Channels ====================


async def manage_channels_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """لوحة إدارة القنوات"""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return


    # auto_publish_state = bot_db.get_setting('auto_publish', '0')

    # auto_publish_icon = "✅" if auto_publish_state == '1' else "❌"


    keyboard = [


        [InlineKeyboardButton("📢 حالة القنوات", callback_data="status_channels"), InlineKeyboardButton("👥 حالة المجموعات", callback_data="status_groups")],

        [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]

    ]


    await _safe_edit_message_text(

        query,

        "📢 **إدارة القنوات والمجموعات**\n\nتحكم في القنوات المضافة والنشر التلقائي.",

        reply_markup=InlineKeyboardMarkup(keyboard),

        parse_mode='Markdown'

    )


# ==================== Status & Toggle ====================


# ==================== Auto Publish Management (New) ====================


async def auto_publish_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """لوحة إدارة النشر التلقائي"""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return

    context.user_data.pop('awaiting_pub_cat_search', None)
    context.user_data.pop(AWAITING_SCHEDULED_FATWA_INPUT_KEY, None)

    # Get settings

    auto_publish = bot_db.get_setting('auto_publish', '0')

    specific_enabled = bot_db.get_setting('auto_publish_specific', '0')
    scheduled_fatwa_number, scheduled_fatwa = _get_scheduled_fatwa()


    # Text Status

    if auto_publish == '1':

        status_text = "✅ مفعل (نشر عشوائي)"

        toggle_icon = "إيقاف 🚫"

    elif specific_enabled == '1':

        # Get category name

        cat_id = bot_db.get_setting('auto_publish_category_id', '')

        cat_name = "غير محدد"

        if cat_id:

            cat = db.get_category(int(cat_id))

            if cat:

                cat_name = cat['name']

        status_text = f"✅ مفعل (نشر محدد: {escape_markdown(cat_name)})"

        # For Main Toggle, if specific is ON, clicking it (Enable) replaces specific? No.

        # Toggle icon logic:

        # If Random is OFF, button says "Enable Random". If ON, "Disable".

        toggle_icon = "تفعيل النشر العشوائي ✅"

    else:

        status_text = "❌ معطل بالكامل"

        toggle_icon = "تفعيل النشر العشوائي ✅"


    random_is_on = auto_publish == '1'

    toggle_random_btn_text = "إيقاف النشر العشوائي 🚫" if random_is_on else "تفعيل النشر العشوائي ✅"
    schedule_btn_text = (
        f"🗓️ جدولة فتوى (#{scheduled_fatwa_number})"
        if scheduled_fatwa_number
        else "🗓️ جدولة فتوى"
    )
    scheduled_status_text = (
        f"🗓️ الجدولة القادمة: فتوى رقم `{scheduled_fatwa_number}` بعنوان **{escape_markdown(scheduled_fatwa['title'])}**\n"
        "سيتم نشرها مرة واحدة في موعد النشر اليومي القادم، ثم سيستمر وضع النشر الحالي كما هو.\n"
        if scheduled_fatwa_number and scheduled_fatwa
        else "🗓️ الجدولة القادمة: لا توجد فتوى مجدولة حالياً.\n"
    )


    keyboard = [

        [InlineKeyboardButton("🚀 نشر الآن", callback_data="force_publish_now"),InlineKeyboardButton(schedule_btn_text, callback_data="schedule_fatwa_once")],

        [InlineKeyboardButton(f"{toggle_random_btn_text}", callback_data="toggle_auto_publish_master"),InlineKeyboardButton("🎯 إعدادات النشر المحدد", callback_data="targeted_publish_panel")],

        [InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]

    ]


    text = (

        "⚙️ **إدارة النشر التلقائي**\n\n"

        f"الحالة الحالية: {status_text}\n"
        f"{scheduled_status_text}"

        "تحكم في خيارات النشر التلقائي من هنا."

    )


    await _safe_edit_message_text(

        query,

        text,

        reply_markup=InlineKeyboardMarkup(keyboard),

        parse_mode='Markdown'

    )


async def toggle_auto_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """تفعيل/تعطيل النشر التلقائي (Global/Random)"""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return


    current = bot_db.get_setting('auto_publish', '0')


    if current == '0':

        # Enable Random -> Disable Specific

        bot_db.set_setting('auto_publish', '1')

        bot_db.set_setting('auto_publish_specific', '0')

    else:

        # Disable Random -> Just disable it

        bot_db.set_setting('auto_publish', '0')


    # Reload panel

    await auto_publish_panel(update, context)


async def force_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """نشر فتوى فوراً"""

    query = update.callback_query

    await query.answer("جاري النشر...")
    if not await _ensure_admin(update, query):
        return


    # Trigger the job logic manually

    # We pass context but 'job' specific args might be missing, so daily_fatwa_job must handle it.

    await daily_fatwa_job(context, force=True, respect_scheduled=False)

    await auto_publish_panel(update, context)


async def start_schedule_fatwa_once(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """طلب رقم فتوى ليتم نشرها مرة واحدة في موعد النشر اليومي القادم."""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return

    context.user_data[AWAITING_SCHEDULED_FATWA_INPUT_KEY] = True
    context.user_data.pop('awaiting_pub_cat_search', None)

    scheduled_fatwa_number, _ = _get_scheduled_fatwa()
    prompt_text = (
        "🗓️ **جدولة فتوى**\n\n"
        "أرسل رقم الفتوى التي تريد نشرها مرة واحدة في موعد النشر اليومي القادم."
    )
    if scheduled_fatwa_number:
        prompt_text += (
            f"\n\nالجدولة الحالية: `{scheduled_fatwa_number}`\n"
            "أرسل رقمًا جديدًا لاستبدالها."
        )

    await _safe_edit_message_text(
        query,
        prompt_text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]]),
        parse_mode='Markdown'
    )


async def targeted_publish_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """لوحة النشر المحدد"""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return

    specific_enabled = bot_db.get_setting('auto_publish_specific', '0') == '1'
    cat_id, category = _get_selected_publish_category()

    if specific_enabled and not cat_id:
        bot_db.set_setting('auto_publish_specific', '0')
        specific_enabled = False

    sp_status_icon = "✅ مفعل" if specific_enabled else "❌ معطل"
    sp_toggle_icon = "إيقاف ❌" if specific_enabled else "تفعيل ✅"

    cat_name = category['name'] if category else "غير محدد"

    selected_topic_ids: List[int] = []
    topic_map: Dict[int, str] = {}
    topic_preview = "اختر تصنيفًا أولاً"

    if cat_id:
        selected_topic_ids, topic_map = _load_targeted_topic_selection(cat_id)
        topic_preview = _build_topic_selection_preview(selected_topic_ids, topic_map)

    keyboard = [
        [InlineKeyboardButton(f"📂 اختيار تصنيف ({cat_name})", callback_data="sel_pub_cat_start")]
    ]

    if cat_id:
        selected_count_label = str(len(selected_topic_ids)) if selected_topic_ids else "الكل"
        keyboard.append([InlineKeyboardButton(f"🧩 اختيار المواضيع ({selected_count_label})", callback_data="sel_pub_top_start")])

    if selected_topic_ids:
        keyboard.append([InlineKeyboardButton("🧹 مسح اختيار المواضيع", callback_data="clear_pub_topics")])

    keyboard.extend([
        [InlineKeyboardButton(f"{sp_toggle_icon}", callback_data="toggle_targeted_publish")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="auto_publish_panel")]
    ])

    text = (
        "🎯 **النشر المحدد**\n\n"
        "عند تفعيل هذا الخيار، سيقوم البوت بنشر الفتاوى من التصنيف المختار فقط.\n"
        "وفي حال تحديد مواضيع، سيكون النشر محصورًا داخل هذه المواضيع ضمن التصنيف.\n\n"
        f"الحالة: {sp_status_icon}\n"
        f"التصنيف المختار: **{escape_markdown(cat_name)}**\n"
        f"المواضيع المختارة: {topic_preview}"
    )

    await _safe_edit_message_text(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )


async def toggle_targeted_publish(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """تفعيل/تعطيل النشر المحدد"""

    query = update.callback_query
    if not await _ensure_admin(update, query):
        return

    current = bot_db.get_setting('auto_publish_specific', '0')

    if current == '0':

        # Check if category is selected before enabling
        cat_id, _ = _get_selected_publish_category()

        if not cat_id:
            await query.answer("⚠️ يرجى اختيار تصنيف أولاً!", show_alert=True)
            return

        # Enable Specific -> Disable Random
        bot_db.set_setting('auto_publish_specific', '1')
        bot_db.set_setting('auto_publish', '0')

    else:

        # Disable Specific -> Just disable it
        bot_db.set_setting('auto_publish_specific', '0')

    await query.answer()

    await targeted_publish_panel(update, context)


async def start_select_publish_category(update: Update, context: ContextTypes.DEFAULT_TYPE, search_query=None):

    """عرض قائمة التصنيفات للاختيار (Pagination)"""

    query = update.callback_query

    # Handle direct message update (from search)

    if query:

        await query.answer()
        if not await _ensure_admin(update, query):
            return

        data = query.data or ""

    else:

        if not await _ensure_admin(update):
            return
        data = ""


    # الخروج من وضع البحث النصي إن كان مفعلاً

    context.user_data.pop('awaiting_pub_cat_search', None)


    page = 0

    if "sel_pub_cat_page_" in data:

        page = int(data.split("sel_pub_cat_page_")[-1])


    # Check for stored search query if not passed explicitly

    if search_query is None and query:

         # If navigating pages, keep existing search?

         # For simplicity, if page navigation, check user_data

         search_query = context.user_data.get('pub_cat_search_query')


    ITEMS_PER_PAGE = 8 # 8 items = 4 rows of 2

    offset = page * ITEMS_PER_PAGE


    categories = db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)

    total_count = db.get_categories_count(search_query=search_query)


    keyboard = []


    # 2-Column Layout

    row = []

    for cat_id, name in categories:

        row.append(InlineKeyboardButton(name, callback_data=f"set_pub_cat_{cat_id}"))

        if len(row) == 2:

            keyboard.append(row)

            row = []

    if row:

        keyboard.append(row)


    # Navigation

    nav = []

    if page > 0:

        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sel_pub_cat_page_{page-1}"))

    if offset + ITEMS_PER_PAGE < total_count:

        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sel_pub_cat_page_{page+1}"))

    if nav:

        keyboard.append(nav)


    # Search & Back

    keyboard.append([InlineKeyboardButton("🔍 بحث عن تصنيف", callback_data="search_pub_cat")])


    # If search is active, allow clearing it

    if search_query:

        keyboard.append([InlineKeyboardButton("🔙 إلغاء البحث", callback_data="clear_pub_cat_search")])


    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="targeted_publish_panel")])


    title = "📂 **اختيار التصنيف**"

    if search_query:

        title += f"\n🔍 نتائج البحث عن: {escape_markdown(search_query)}"

    else:

        title += "\nاختر التصنيف الذي تريد النشر منه:"


    reply_markup = InlineKeyboardMarkup(keyboard)


    if query:

        await _safe_edit_message_text(

            query,

            title,

            reply_markup=reply_markup,

            parse_mode='Markdown'

        )

    else:

        # From text message

        await update.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')


async def start_search_publish_category(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """بدء وضع البحث"""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return


    context.user_data['awaiting_pub_cat_search'] = True
    context.user_data.pop(AWAITING_SCHEDULED_FATWA_INPUT_KEY, None)


    await _safe_edit_message_text(

        query,

        "🔍 **بحث عن تصنيف**\n\nأرسل اسم التصنيف (أو جزء منه) للبحث:",

        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="sel_pub_cat_start")]])

    )


async def clear_publish_category_search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """إلغاء البحث"""
    if not await _ensure_admin(update, update.callback_query):
        return

    context.user_data.pop('pub_cat_search_query', None)

    context.user_data.pop('awaiting_pub_cat_search', None) # Safety

    await start_select_publish_category(update, context)


async def handle_publish_category_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """استقبال مدخلات النص الخاصة بإدارة النشر التلقائي."""
    if not await _ensure_admin(update):
        return

    if context.user_data.get(AWAITING_SCHEDULED_FATWA_INPUT_KEY):

        input_text = (update.message.text or "").strip()
        try:
            fatwa_number = int(input_text)
        except (TypeError, ValueError):
            await update.message.reply_text(
                "⚠️ أرسل رقم فتوى صحيحًا فقط.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]])
            )
            return

        fatwa = db.get_fatwa_by_number(fatwa_number)
        if not fatwa:
            await update.message.reply_text(
                f"❌ لم يتم العثور على فتوى برقم: {fatwa_number}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]])
            )
            return

        if fatwa.get('status') != 'published':
            await update.message.reply_text(
                "⚠️ لا يمكن جدولة فتوى غير منشورة. انشر الفتوى أولاً ثم أعد المحاولة.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إلغاء", callback_data="auto_publish_panel")]])
            )
            return

        bot_db.set_setting(SCHEDULED_FATWA_SETTING_KEY, str(fatwa_number))
        context.user_data.pop(AWAITING_SCHEDULED_FATWA_INPUT_KEY, None)

        daily_time = bot_db.get_setting('daily_publish_time', '12:00') or '12:00'
        await update.message.reply_text(
            (
                f"✅ تمت جدولة الفتوى رقم `{fatwa_number}` للنشر مرة واحدة "
                f"في موعد النشر اليومي القادم ({daily_time})."
            ),
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ إدارة النشر التلقائي", callback_data="auto_publish_panel")]])
        )
        return

    if not context.user_data.get('awaiting_pub_cat_search'):

        return # Not in search mode


    query_text = update.message.text.strip()

    context.user_data['pub_cat_search_query'] = query_text

    context.user_data['awaiting_pub_cat_search'] = False # consume state


    await start_select_publish_category(update, context, search_query=query_text)


async def set_publish_category(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """حفظ التصنيف المختار"""

    query = update.callback_query
    if not await _ensure_admin(update, query):
        return

    cat_id = int(query.data.split('set_pub_cat_')[-1])
    category = db.get_category(cat_id)

    if not category:
        await query.answer("⚠️ التصنيف غير موجود", show_alert=True)
        return

    prev_cat_id, _ = _get_selected_publish_category()
    bot_db.set_setting('auto_publish_category_id', str(cat_id))

    if prev_cat_id != cat_id:
        bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')

    context.user_data.pop('pub_cat_search_query', None)
    context.user_data.pop('awaiting_pub_cat_search', None)

    await query.answer("✅ تم اختيار التصنيف")
    await start_select_publish_topics(update, context, page=0)


async def start_select_publish_topics(update: Update, context: ContextTypes.DEFAULT_TYPE, page: Optional[int] = None):

    """عرض مواضيع التصنيف المختار مع دعم اختيار متعدد"""

    query = update.callback_query

    if query:
        try:
            await query.answer()
        except Exception:
            pass
        if not await _ensure_admin(update, query):
            return
        data = query.data or ""
    else:
        if not await _ensure_admin(update):
            return
        data = ""

    cat_id, category = _get_selected_publish_category()
    if not cat_id or not category:
        if query:
            await query.answer("⚠️ يرجى اختيار تصنيف أولاً", show_alert=True)
            await targeted_publish_panel(update, context)
        return

    if page is None:
        page = 0
        if data.startswith("sel_pub_top_page_"):
            try:
                page = int(data.split("sel_pub_top_page_")[-1])
            except (TypeError, ValueError):
                page = 0

    page = max(page, 0)

    selected_topic_ids, topic_map = _load_targeted_topic_selection(cat_id)

    items_per_page = 8
    offset = page * items_per_page
    topics = db.get_topics_by_category(cat_id, limit=items_per_page, offset=offset)
    total_count = db.get_topics_count(cat_id)

    keyboard = []

    row = []
    for topic_id, name in topics:
        label = f"✅ {name}" if topic_id in selected_topic_ids else name
        row.append(InlineKeyboardButton(label, callback_data=f"toggle_pub_top_{topic_id}_{page}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sel_pub_top_page_{page-1}"))
    if offset + items_per_page < total_count:
        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sel_pub_top_page_{page+1}"))
    if nav:
        keyboard.append(nav)

    if selected_topic_ids:
        keyboard.append([InlineKeyboardButton("🧹 مسح الاختيار", callback_data="clear_pub_topics")])

    keyboard.append([InlineKeyboardButton("✅ تم", callback_data="targeted_publish_panel")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="targeted_publish_panel")])

    if total_count == 0:
        current_selection_text = "لا توجد مواضيع داخل هذا التصنيف حالياً"
    else:
        current_selection_text = _build_topic_selection_preview(selected_topic_ids, topic_map)

    text = (
        "🧩 **اختيار المواضيع**\n\n"
        f"التصنيف: **{escape_markdown(category['name'])}**\n"
        f"المحدد الآن: {current_selection_text}\n\n"
        "اضغط على الموضوع للتحديد أو إلغاء التحديد."
    )

    if query:
        await _safe_edit_message_text(
            query,
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


async def toggle_publish_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """تبديل اختيار موضوع ضمن التصنيف المحدد"""

    query = update.callback_query
    if not await _ensure_admin(update, query):
        return

    data = query.data or ""
    parts = data.split("_")

    if len(parts) < 4:
        await query.answer("⚠️ بيانات الموضوع غير صالحة", show_alert=True)
        return

    try:
        topic_id = int(parts[3])
    except (TypeError, ValueError):
        await query.answer("⚠️ بيانات الموضوع غير صالحة", show_alert=True)
        return

    page = 0
    if len(parts) > 4:
        try:
            page = int(parts[4])
        except (TypeError, ValueError):
            page = 0

    cat_id, _ = _get_selected_publish_category()
    if not cat_id:
        await query.answer("⚠️ يرجى اختيار تصنيف أولاً", show_alert=True)
        await targeted_publish_panel(update, context)
        return

    selected_topic_ids, topic_map = _load_targeted_topic_selection(cat_id)

    if topic_id not in topic_map:
        await query.answer("⚠️ الموضوع لا ينتمي للتصنيف المحدد", show_alert=True)
        return

    if topic_id in selected_topic_ids:
        selected_topic_ids.remove(topic_id)
    else:
        selected_topic_ids.append(topic_id)

    bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, _serialize_int_list_setting(selected_topic_ids))

    await start_select_publish_topics(update, context, page=max(page, 0))


async def clear_publish_topics_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """مسح المواضيع المختارة في النشر المحدد"""

    query = update.callback_query
    if not await _ensure_admin(update, query):
        return

    bot_db.set_setting(TARGETED_TOPICS_SETTING_KEY, '')
    await start_select_publish_topics(update, context, page=0)


async def show_channel_status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """عرض قائمة القنوات/المجموعات"""

    query = update.callback_query

    if not await _ensure_admin(update, query):
        return


    # Data format: status_channels OR status_groups
    target_type = 'channel' if 'channels' in query.data else 'group'

    # المطلوب: فتح النشطة مباشرة عند الضغط على حالة القنوات/المجموعات
    await list_channels_handler(
        update,
        context,
        c_type_override=target_type,
        status_override='active',
        page_override=0,
    )


async def list_channels_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    c_type_override: Optional[str] = None,
    status_override: Optional[str] = None,
    page_override: Optional[int] = None,
):

    """سرد القنوات (نشطة/غير نشطة)"""

    query = update.callback_query

    await query.answer()
    if not await _ensure_admin(update, query):
        return


    if c_type_override and status_override is not None:
        c_type = c_type_override
        status = status_override
        page = page_override if page_override is not None else 0
    else:
        # list_channel_active or list_channel_active_0
        parts = query.data.split('_')
        if len(parts) < 3:
            return

        c_type = parts[1]  # channel or group
        status = parts[2]  # active or inactive
        if c_type not in {"channel", "group"} or status not in {"active", "inactive"}:
            return

        page = 0
        if len(parts) >= 4:
            try:
                page = int(parts[3])
            except (TypeError, ValueError):
                page = 0

    # Use DB status مباشرة لتسريع الاستجابة داخل القوائم.
    channels = bot_db.get_channels(status=status, chat_type=c_type)

    # ترتيب من الأحدث إلى الأقدم
    channels.sort(
        key=lambda row: (
            str(row.get('added_at') or ''),
            int(row.get('chat_id', 0)),
        ),
        reverse=True,
    )

    ITEMS_PER_PAGE = 10
    total_count = len(channels)
    total_pages = max((total_count - 1) // ITEMS_PER_PAGE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * ITEMS_PER_PAGE
    page_items = channels[start:start + ITEMS_PER_PAGE]

    is_channel = c_type == 'channel'
    icon = "📢" if is_channel else "👥"
    entity_label = "القنوات المشتركة" if is_channel else "المجموعات المشتركة"
    state_label = "النشطة" if status == 'active' else "غير النشطة"

    lines = [
        f"{icon} <b>{entity_label} {state_label} [{total_count}]</b>",
        f"الصفحة: {page + 1}/{total_pages}",
        "",
    ]

    if not page_items:
        lines.append("لا توجد عناصر في هذه القائمة.")
    else:
        item_state = "نشطة" if status == 'active' else "غير نشطة"
        for idx, ch in enumerate(page_items, start=start + 1):
            title = html.escape(ch.get('title') or '-')
            username = f"@{html.escape(ch['username'])}" if ch.get('username') else "بدون يوزر"
            lines.append(
                f"{idx}. {title} | {username} | <code>{ch['chat_id']}</code> | {item_state}"
            )

    text = "\n".join(lines)

    prev_page = page - 1 if page > 0 else page
    next_page = page + 1 if page < total_pages - 1 else page
    toggle_is_active = status == 'active'
    toggle_label = "⚫ غير النشطة" if toggle_is_active else "✅ النشطة"
    toggle_status = "inactive" if toggle_is_active else "active"

    keyboard = [[
        InlineKeyboardButton("⬅️ السابق", callback_data=f"list_{c_type}_{status}_{prev_page}"),
        InlineKeyboardButton(toggle_label, callback_data=f"list_{c_type}_{toggle_status}_0"),
        InlineKeyboardButton("التالي ➡️", callback_data=f"list_{c_type}_{status}_{next_page}"),
    ]]

    # إذا كانت غير نشطة، نعرض زر تنظيف
    if status == 'inactive' and total_count:
        keyboard.append([InlineKeyboardButton("🗑️ حذف الكل (خروج)", callback_data=f"cleanup_{c_type}")])

    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_channels")])

    await _safe_edit_message_text(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def cleanup_inactive(update: Update, context: ContextTypes.DEFAULT_TYPE):

    """Remove inactive channels/groups only when the bot actually left."""

    query = update.callback_query

    await query.answer("جاري التنظيف...", cache_time=0)
    if not await _ensure_admin(update, query):
        return

    c_type = query.data.split('_')[1]  # cleanup_channel
    if c_type not in {"channel", "group"}:
        await query.answer("طلب غير صالح.", show_alert=True)
        return

    channels = bot_db.get_channels(status='inactive', chat_type=c_type)

    removed_count = 0
    kept_count = 0
    reactivated_count = 0

    for ch in channels:
        chat_id = ch['chat_id']
        should_remove = False

        try:
            member = await context.bot.get_chat_member(chat_id, context.bot.id)

            if member.status in (ChatMember.LEFT, ChatMember.KICKED):
                should_remove = True
            else:
                has_perm = _has_manage_messages_permission(c_type, member)
                new_status = 'active' if has_perm else 'inactive'

                if ch.get('status') != new_status:
                    bot_db.update_channel_status(chat_id, new_status)

                if new_status == 'active':
                    reactivated_count += 1
                else:
                    kept_count += 1

        except Exception as e:
            if _is_bot_removed_chat_error(e):
                should_remove = True
            else:
                kept_count += 1
                logger.debug(f"Skipping cleanup for chat {chat_id}; could not verify removal state: {e}")

        if should_remove:
            if bot_db.remove_channel(chat_id):
                removed_count += 1

            try:
                await context.bot.leave_chat(chat_id)
            except Exception as leave_error:
                logger.debug(f"Skipping leave_chat for {chat_id}: {leave_error}")

    await query.message.reply_text(
        "✅ اكتمل تنظيف غير النشطة.\n"
        f"🗑️ المحذوف (خروج البوت فعليًا): {removed_count}\n"
        f"🔄 عاد للنشاط تلقائيًا: {reactivated_count}\n"
        f"📌 تم الإبقاء عليه (نقص صلاحيات/تعذر التحقق): {kept_count}"
    )

    await list_channels_handler(
        update,
        context,
        c_type_override=c_type,
        status_override='inactive',
        page_override=0,
    )


# ==================== Scheduled Jobs ====================


async def daily_fatwa_job(
    context: ContextTypes.DEFAULT_TYPE,
    force: bool = False,
    respect_scheduled: bool = True,
):

    """مهمة النشر اليومي أو النشر الفوري اليدوي."""

    random_enabled = bot_db.get_setting('auto_publish', '0') == '1'
    specific_enabled = bot_db.get_setting('auto_publish_specific', '0') == '1'
    maintenance_enabled = bot_db.get_setting('maintenance_mode', '0') == '1'
    target_cat_id = None
    target_topic_ids: List[int] = []
    scheduled_fatwa_number = None
    scheduled_publish_used = False
    publish_mode_label = "نشر فوري يدوي" if force else "نشر عشوائي"
    fatwa = None

    if respect_scheduled:
        scheduled_fatwa_number, fatwa = _get_scheduled_fatwa()
        if fatwa:
            scheduled_publish_used = True
            publish_mode_label = "جدولة فتوى لمرة واحدة"

    if fatwa is None and not force and not random_enabled and not specific_enabled:
        return

    if fatwa is None:
        if specific_enabled:
            target_cat_id, _ = _get_selected_publish_category()

            if not target_cat_id:
                logger.info("Auto-Publish: Specific mode enabled but no valid category selected.")
                return

            target_topic_ids, _ = _load_targeted_topic_selection(target_cat_id)
            publish_mode_label = "نشر محدد"
        elif random_enabled:
            publish_mode_label = "نشر عشوائي"

        fatwa = db.get_random_published_fatwa(
            category_id=target_cat_id,
            topic_ids=target_topic_ids if target_topic_ids else None
        )

        if not fatwa:
            logger.info(
                f"Auto-Publish: No published fatwas found (Category: {target_cat_id}, Topics: {target_topic_ids or 'ALL'})."
            )
            return

    try:
        text_to_send, is_long = build_fatwa_preview_text(fatwa, max_length=3600)

        reply_markup = create_published_fatwa_keyboard(
            fatwa=fatwa,
            bot_username=context.bot.username,
            is_long=is_long,
            continue_label="تابع القراءة",
        )

        channels = [] if maintenance_enabled else bot_db.get_channels(status='active')
        sent_to = set()
        count_channels = 0

        for ch in channels:
            try:
                if ch['chat_id'] in sent_to:
                    continue

                sent_msg = await context.bot.send_message(
                    chat_id=ch['chat_id'],
                    text=text_to_send,
                    reply_markup=reply_markup
                )

                _register_delivery_message(context, fatwa['id'], ch['chat_id'], sent_msg.message_id)
                sent_to.add(ch['chat_id'])
                count_channels += 1
            except Exception as e:
                logger.error(f"Failed to auto-publish to channel {ch.get('chat_id')}: {e}")
                if "Forbidden" in str(e) or "chat not found" in str(e).lower():
                    bot_db.update_channel_status(ch['chat_id'], 'inactive')

        if count_channels > 0:
            db.increment_views_by(fatwa['id'], count_channels)

        users = [] if maintenance_enabled else list(dict.fromkeys(bot_db.get_all_bot_users()))
        count_users = 0
        count_admins = 0

        for user_id in users:
            try:
                if user_id in sent_to:
                    continue

                sent_msg = await context.bot.send_message(
                    chat_id=user_id,
                    text=text_to_send,
                    reply_markup=reply_markup
                )

                _register_delivery_message(context, fatwa['id'], user_id, sent_msg.message_id)
                sent_to.add(user_id)
                count_users += 1
            except Exception as e:
                logger.debug(f"Skipping subscriber {user_id} during auto-publish: {e}")

        admins = bot_db.get_admins()
        should_send_admin_copy = maintenance_enabled or (not users)
        if should_send_admin_copy:
            for admin in admins:
                try:
                    admin_id = int(admin['user_id'])
                    if admin_id in sent_to:
                        continue

                    sent_msg = await context.bot.send_message(
                        chat_id=admin_id,
                        text=text_to_send,
                        reply_markup=reply_markup
                    )
                    _register_delivery_message(context, fatwa['id'], admin_id, sent_msg.message_id)
                    sent_to.add(admin_id)
                    count_admins += 1
                except Exception as e:
                    logger.debug(f"Skipping admin delivery copy for {admin.get('user_id')}: {e}")

        logger.debug(f"Auto-published fatwa {fatwa['id']}: Sent to {count_channels} channels and {count_users} subscribers.")

        total_sent = count_channels + count_users + count_admins
        if maintenance_enabled:
            report_text = (
                "🛠️ **تقرير النشر التلقائي اليومي**\n\n"
                "وضع الصيانة: **مفعّل**\n"
                f"نوع النشر: **{publish_mode_label}**\n"
                "تم إرسال الفتوى للمسؤولين فقط.\n\n"
                "📜 **الفتوى المنشورة:**\n"
                f"رقم: `{fatwa.get('fatwa_number', fatwa['id'])}`\n"
                f"العنوان: {escape_markdown(fatwa['title'])}\n\n"
                "📊 **إحصائيات الإرسال:**\n"
                f"🧑‍💼 المسؤولون: {count_admins}\n"
                f"✅ المجموع: {total_sent}"
            )
        else:
            report_text = (
                "✅ **تقرير النشر التلقائي اليومي**\n\n"
                f"نوع النشر: **{publish_mode_label}**\n\n"
                "📜 **الفتوى المنشورة:**\n"
                f"رقم: `{fatwa.get('fatwa_number', fatwa['id'])}`\n"
                f"العنوان: {escape_markdown(fatwa['title'])}\n\n"
                "📊 **إحصائيات الإرسال:**\n"
                f"📢 القنوات/المجموعات: {count_channels}\n"
                f"👤 المشتركين: {count_users}\n"
                f"🧑‍💼 المسؤولون (نسخة احتياطية): {count_admins}\n"
                f"✅ المجموع: {total_sent}"
            )

        for admin in admins:
            try:
                await context.bot.send_message(
                    chat_id=admin['user_id'],
                    text=report_text,
                    parse_mode='Markdown',
                    reply_markup=_build_delivery_report_keyboard(fatwa['id'])
                )
            except Exception as e:
                logger.error(f"Failed to send auto-publish report to admin {admin.get('user_id')}: {e}")
    finally:
        if scheduled_publish_used and scheduled_fatwa_number:
            _clear_scheduled_fatwa()


async def weekly_fatwa_report_job(context: ContextTypes.DEFAULT_TYPE):

    """Send a weekly report to users with new fatwas per scholar."""

    try:

        now_local = datetime.now().astimezone()

        now_utc = datetime.utcnow()


        # Weekday gate (0=Mon .. 6=Sun)

        configured_weekday = context.bot_data.get("weekly_report_weekday")

        if configured_weekday is not None:

            if now_local.weekday() != int(configured_weekday):

                return


        last_run_raw = bot_db.get_setting('weekly_report_last_run', '')

        start_utc = now_utc - timedelta(days=7)

        if last_run_raw:

            try:

                last_run = datetime.fromisoformat(last_run_raw)

                # Prevent duplicate send on the same day

                if last_run.date() == now_utc.date():

                    return

                start_utc = last_run

            except Exception as e:
                logger.debug(f"Invalid weekly_report_last_run value '{last_run_raw}': {e}")


        start_ts = start_utc.strftime("%Y-%m-%d %H:%M:%S")

        start_label = start_utc.strftime("%Y-%m-%d")

        end_label = now_utc.strftime("%Y-%m-%d")


        counts = db.get_new_fatwa_counts_by_scholar_since(start_ts)

        total_new = sum(item.get('count', 0) for item in counts)


        if total_new == 0:

            text = (

                "📚 تقرير الفتاوى الأسبوعي\n\n"

                f"الفترة: {start_label} إلى {end_label}\n"

                "لا توجد فتاوى جديدة خلال هذا الأسبوع."

            )

        else:

            lines = [

                "📚 تقرير الفتاوى الأسبوعي",

                "",

                f"الفترة: {start_label} إلى {end_label}",

                f"إجمالي الفتاوى الجديدة: {total_new}",

                "",

            ]

            for item in counts:

                scholar_name = item.get('scholar_name') or "غير محدد"

                count = item.get('count', 0)

                lines.append(f"- {scholar_name}: {count} فتوى جديدة")

            text = "\n".join(lines)


        message_parts = split_long_message(text)


        users = list(dict.fromkeys(bot_db.get_active_user_ids()))

        if not users:

            bot_db.set_setting('weekly_report_last_run', now_utc.isoformat())

            return


        for user_id in users:

            try:

                for i, part in enumerate(message_parts):

                    await safe_send_message(context.bot, user_id, part)

            except Exception as e:

                err = str(e).lower()

                if "forbidden" in err or "blocked" in err or "deactivated" in err:

                    bot_db.set_user_blocked(user_id, True)

                continue


        bot_db.set_setting('weekly_report_last_run', now_utc.isoformat())

    except Exception as e:

        logger.error(f"Weekly report job failed: {e}")
