"""
معالجات الفتاوى (handlers/fatwa.py)
-----------------------------------
يحتوي على:
- إضافة فتوى جديدة (Conversation).
- عرض فتوى (View).
- نشر فتوى (Publish).
- تعديل وحذف الفتاوى.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import *
from core.utils import (
    sanitize_input,
    format_fatwa_card,
    format_fatwa_content,
    build_fatwa_preview_text,
    split_long_message,
    create_main_keyboard,
    format_full_fatwa_for_copy,
    back_to_main_keyboard,
    back_to_search_keyboard,
    escape_markdown,
    safe_reply_text,
    safe_edit_message_text,
)
from core.keyboards import (
    create_fatwa_view_keyboard,
    create_published_fatwa_keyboard,
    back_to_main_keyboard as kb_back_main,
)
from handlers.general import cancel_operation, start_refresh, back_to_main

import logging
import re
from types import SimpleNamespace

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

_DELIVERY_LOG_KEY = "fatwa_delivery_log"
_PENDING_DUPLICATE_ANSWER_KEY = "pending_duplicate_answer"
_PENDING_DUPLICATE_MATCHES_KEY = "pending_duplicate_matches"


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

# ==================== Helpers (Add Flow UX) ====================

def _set_add_step(context: ContextTypes.DEFAULT_TYPE, step: str):
    history = context.user_data.setdefault('add_step_history', [])
    if not history or history[-1] != step:
        history.append(step)
    context.user_data['add_step'] = step


def _pop_add_step(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    history = context.user_data.get('add_step_history', [])
    if len(history) <= 1:
        return None
    history.pop()
    return history[-1] if history else None


def _add_flow_nav_keyboard(include_back_step: bool = True) -> InlineKeyboardMarkup:
    row = []
    if include_back_step:
        row.append(InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"))
    row.append(InlineKeyboardButton("❌ إلغاء", callback_data="cancel"))
    return InlineKeyboardMarkup([row])

def _fatwa_text_nav_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تم الإدخال", callback_data="confirm_fatwa_text")],
        [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])


def _duplicate_fatwa_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ إلغاء الإدخال", callback_data="dup_cancel_add")],
        [InlineKeyboardButton("✅ إكمال الإدخال", callback_data="dup_continue_add")],
    ])

def _source_title_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_title")],
        [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])


async def _send_add_prompt(update_obj, text: str, reply_markup: InlineKeyboardMarkup, **kwargs):
    if isinstance(update_obj, Update) and update_obj.callback_query:
        await safe_edit_message_text(update_obj.callback_query, text, reply_markup=reply_markup, **kwargs)
    elif hasattr(update_obj, 'edit_message_text'):
        await safe_edit_message_text(update_obj, text, reply_markup=reply_markup, **kwargs)
    elif isinstance(update_obj, Update) and update_obj.message:
        await safe_reply_text(update_obj.message, text, reply_markup=reply_markup, **kwargs)
    elif hasattr(update_obj, 'reply_text'):
        await safe_reply_text(update_obj, text, reply_markup=reply_markup, **kwargs)


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_view_back_button(query, context: ContextTypes.DEFAULT_TYPE, fatwa_id: int | None = None) -> InlineKeyboardButton:
    data = query.data or ""
    if fatwa_id is not None and not data.startswith("view_"):
        ctx_view = _resolve_view_context_data(context, fatwa_id, data)
        if ctx_view:
            data = ctx_view

    # Search results
    if data.endswith('_search'):
        if context.user_data.get('last_search_results'):
            last_page = context.user_data.get('last_search_page', 0)
            return InlineKeyboardButton("🔙 رجوع للنتائج", callback_data=f"res_page_{last_page}")
        return InlineKeyboardButton("🔙 رجوع للبحث", callback_data="search_fatwas")

    parts = data.split('_')
    if len(parts) >= 3:
        source = parts[2]
        if source == "drafts":
            page = _safe_int(parts[3]) if len(parts) > 3 else 0
            return InlineKeyboardButton("📝 رجوع للمسودات", callback_data=f"admin_drafts_{page}")
        if source == "dups":
            page = _safe_int(parts[3]) if len(parts) > 3 else 0
            return InlineKeyboardButton("🔄 رجوع للمكررة", callback_data=f"admin_duplicates_{page}")
        if source == "missing":
            link_type = parts[3] if len(parts) > 3 else "source"
            page = _safe_int(parts[4]) if len(parts) > 4 else 0
            return InlineKeyboardButton("🔗 رجوع للروابط الناقصة", callback_data=f"missing_links_{link_type}_{page}")
        if source == "fav":
            page = _safe_int(parts[3]) if len(parts) > 3 else 0
            return InlineKeyboardButton("⭐ رجوع للمفضلة", callback_data=f"fav_page_{page}")
        if source == "topfav":
            return InlineKeyboardButton("🌟 رجوع للمفضلة", callback_data="top_favorites")
        if source == "random":
            return InlineKeyboardButton("🔙 رجوع للمطالعة", callback_data="browse_fatwas")

    return InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")


def _extract_context_suffix(data: str, fatwa_id: int) -> str | None:
    if not data:
        return None
    parts = data.split('_')
    id_str = str(fatwa_id)
    if id_str in parts:
        idx = parts.index(id_str)
        if idx < len(parts) - 1:
            return "_".join(parts[idx + 1:])
    return None


def _resolve_view_context_data(context: ContextTypes.DEFAULT_TYPE, fatwa_id: int, data: str | None = None) -> str | None:
    suffix = _extract_context_suffix(data or "", fatwa_id)
    if suffix:
        return f"view_{fatwa_id}_{suffix}"

    last_view = context.user_data.get('last_view_data')
    if context.user_data.get('last_view_fatwa_id') == fatwa_id and isinstance(last_view, str) and last_view.startswith('view_'):
        return last_view

    edit_view = context.user_data.get('edit_view_context')
    if isinstance(edit_view, str) and edit_view.startswith(f"view_{fatwa_id}_"):
        return edit_view
    return None

# ==================== عرض الفتوى ====================

async def show_random_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض فتوى عشوائية من شاشة المطالعة مع دعم زر فتوى أخرى."""
    query = update.callback_query
    await query.answer()

    match = re.match(r'^random_fatwa(?:_(\d+))?$', query.data or "")
    excluded_ids = []
    if match and match.group(1):
        excluded_ids.append(int(match.group(1)))

    public_only = not bot_db.is_admin(update.effective_user.id)
    fatwa = db.get_random_fatwa(public_only=public_only, excluded_fatwa_ids=excluded_ids)
    if not fatwa and excluded_ids:
        fatwa = db.get_random_fatwa(public_only=public_only)

    if not fatwa:
        await query.edit_message_text(
            "❌ لا توجد فتاوى متاحة للمطالعة العشوائية حالياً.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 رجوع للمطالعة", callback_data="browse_fatwas")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
            ])
        )
        return

    if excluded_ids and fatwa["id"] == excluded_ids[0]:
        try:
            await query.answer("هذه الفتوى الوحيدة المتاحة حالياً.", show_alert=False)
        except Exception:
            pass

    return await view_fatwa(
        update,
        context,
        fatwa_id=fatwa["id"],
        view_context_data=f"view_{fatwa['id']}_random",
    )


async def continue_reading_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض النص الكامل لفتوى كانت معروضة كمقتطف داخل البوت."""
    query = update.callback_query

    match = re.match(r'^continue_read_(\d+)(?:_(.+))?$', query.data or "")
    if not match:
        await query.answer("❌ تعذر متابعة القراءة.", show_alert=True)
        return

    await query.answer()

    fatwa_id = int(match.group(1))
    suffix = match.group(2) or ""
    view_context_data = f"view_{fatwa_id}_{suffix}" if suffix else f"view_{fatwa_id}"

    return await view_fatwa(
        update,
        context,
        fatwa_id=fatwa_id,
        view_context_data=view_context_data,
        force_full=True,
    )


async def view_fatwa(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    fatwa_id: int = None,
    view_context_data: str | None = None,
    force_full: bool = False,
):
    """عرض فتوى كاملة"""
    from core.utils import split_long_message

    query = update.callback_query
    if view_context_data is None:
        await query.answer()

    try:
        if fatwa_id is None:
            # Handle both view_{id} and view_{id}_search
            parts = query.data.split('_')
            fatwa_id = int(parts[1])

        current_view_data = view_context_data or query.data or f"view_{fatwa_id}"

        fatwa = db.get_fatwa(fatwa_id)

        if not fatwa:
            # رجوع إلى واجهة البحث عن الفتاوى
            await query.edit_message_text("❌ الفتوى غير موجودة.", reply_markup=back_to_search_keyboard("🔙 رجوع"))
            return

        # تحقق الصلاحيات قبل عرض المسودات
        is_admin = bot_db.is_admin(update.effective_user.id)
        if not is_admin and fatwa.get('status') != 'published':
            back_btn = _build_view_back_button(SimpleNamespace(data=current_view_data), context, fatwa_id=fatwa_id)
            await query.edit_message_text(
                "❌ هذه الفتوى غير منشورة.",
                reply_markup=InlineKeyboardMarkup([[back_btn]])
            )
            return

        # زيادة المشاهدات
        if not force_full:
            db.increment_views(fatwa_id)

        # تجهيز النص (مع معاينة للفتاوى الطويلة)
        preview_text, is_long = build_fatwa_preview_text(fatwa, max_length=3600)
        text = format_fatwa_content(fatwa, use_markdown=False) if force_full else (preview_text if is_long else format_fatwa_content(fatwa, use_markdown=False))

        # الأزرار
        # --- استخدام Keyboard Builder الموحد ---
        is_favorite = bot_db.is_favorite(update.effective_user.id, fatwa_id)

        # تحديد زر الرجوع
        back_btn = None
        # _build_view_back_button returns an InlineKeyboardButton, define logic to pass it or extract data
        # Actually _build_view_back_button is complex and specific to view_fatwa context (history traversal)
        # So we should keep using it to generate the button, then pass it to the builder.

        context.user_data['last_view_data'] = current_view_data
        context.user_data['last_view_fatwa_id'] = fatwa_id
        back_btn = _build_view_back_button(SimpleNamespace(data=current_view_data), context, fatwa_id=fatwa_id)

        # Extract context suffix for admin actions
        suffix = _extract_context_suffix(current_view_data, fatwa_id)
        continue_reading_callback_data = None
        if is_long and not force_full:
            continue_reading_callback_data = f"continue_read_{fatwa_id}_{suffix}" if suffix else f"continue_read_{fatwa_id}"

        reply_markup = create_fatwa_view_keyboard(
            fatwa=fatwa,
            is_admin=is_admin,
            is_favorite=is_favorite,
            back_button=back_btn,
            context_suffix=suffix or "",
            continue_reading_callback_data=continue_reading_callback_data,
            random_callback_data=f"random_fatwa_{fatwa_id}" if suffix == "random" else None,
        )
        # --- نهاية البناء ---

        # تقسيم الرسالة إذا كانت طويلة
        message_parts = split_long_message(text)

        # إرسال الجزء الأول
        await query.edit_message_text(
            message_parts[0],
            reply_markup=reply_markup if len(message_parts) == 1 else None,
            disable_web_page_preview=True
        )

        # إرسال الأجزاء المتبقية
        for i, part in enumerate(message_parts[1:], 1):
            is_last = (i == len(message_parts) - 1)
            await query.message.reply_text(
                part,
                reply_markup=reply_markup if is_last else None,
                disable_web_page_preview=True
            )


    except Exception as e:
        logger.error(f"Error viewing fatwa: {e}")
        try:
            await query.edit_message_text("❌ حدث خطأ أثناء عرض الفتوى.", reply_markup=back_to_main_keyboard())
        except Exception as edit_error:
            # If editing fails, fallback to sending a new message.
            logger.warning(f"Failed to edit message while handling fatwa error: {edit_error}")
            try:
                await query.message.reply_text("❌ حدث خطأ أثناء عرض الفتوى.", reply_markup=back_to_main_keyboard())
            except Exception as send_error:
                logger.error(f"Failed to send fallback fatwa error message: {send_error}")

async def show_related_fatwas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض فتاوى ذات صلة بالفتوى الحالية."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    try:
        fatwa_id = int(data.split('_')[-1])
    except (IndexError, ValueError):
        await safe_edit_message_text(query, "❌ لم يتم العثور على الفتوى.", reply_markup=back_to_main_keyboard())
        return

    fatwa = db.get_fatwa(fatwa_id)
    if not fatwa:
        await safe_edit_message_text(query, "❌ الفتوى غير موجودة.", reply_markup=back_to_main_keyboard())
        return

    public_only = not bot_db.is_admin(update.effective_user.id)
    related = db.get_related_fatwas(fatwa_id, limit=5, public_only=public_only)

    fatwa_num = fatwa.get('fatwa_number', fatwa_id)
    if related:
        text = f"🔗 فتاوى ذات صلة بالفتوى رقم {fatwa_num}\n\n"
        for item in related:
            text += format_fatwa_card(item, use_markdown=False) + "\n\n"
    else:
        text = f"❌ لا توجد فتاوى ذات صلة بالفتوى رقم {fatwa_num} حالياً."

    keyboard = []
    if related:
        row = []
        for item in related:
            fid = item['id']
            num = item.get('fatwa_number', fid)
            row.append(InlineKeyboardButton(f"📖 عرض الفتوى #{num}", callback_data=f"view_{fid}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton("🔙 رجوع للفتوى", callback_data=f"view_{fatwa_id}"),
        InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    parts = split_long_message(text)
    await safe_edit_message_text(
        query,
        parts[0],
        reply_markup=reply_markup if len(parts) == 1 else None,
        disable_web_page_preview=True
    )
    if len(parts) > 1:
        for i, part in enumerate(parts[1:], 1):
            is_last = (i == len(parts) - 1)
            await safe_reply_text(
                query.message,
                part,
                reply_markup=reply_markup if is_last else None,
                disable_web_page_preview=True
            )

async def send_fatwa_message(update: Update, context: ContextTypes.DEFAULT_TYPE, fatwa_id: int):
    """إرسال فتوى كرسالة جديدة (للروابط العميقة)"""
    from core.utils import split_long_message

    try:
        fatwa = db.get_fatwa(fatwa_id)
        if not fatwa:
            await update.message.reply_text("❌ الفتوى غير موجودة.", reply_markup=create_main_keyboard())
            return

        # تحقق الصلاحيات قبل عرض المسودات
        is_admin = bot_db.is_admin(update.effective_user.id)
        if not is_admin and fatwa.get('status') != 'published':
            await update.message.reply_text("❌ هذه الفتوى غير منشورة.", reply_markup=create_main_keyboard(is_admin))
            return

        # زيادة المشاهدات
        db.increment_views(fatwa_id)

        # تجهيز النص
        text = format_fatwa_content(fatwa, use_markdown=False)

        # الأزرار
        keyboard = []
        # is_admin already computed above

        # أزرار الروابط
        link_buttons = []
        if fatwa.get('source_url'):
            link_buttons.append(InlineKeyboardButton("📚 الانتقال للمصدر", url=fatwa['source_url']))
        if fatwa.get('audio_url'):
            link_buttons.append(InlineKeyboardButton("🎧 سماع الصوتية", url=fatwa['audio_url']))

        if link_buttons:
            keyboard.append(link_buttons)

        # أزرار المفضلة والإبلاغ
        action_buttons = []
        is_fav = bot_db.is_favorite(update.effective_user.id, fatwa_id)
        fav_text = "❌ حذف من المفضلة" if is_fav else "⭐ مفضلة"
        action_buttons.append(InlineKeyboardButton(fav_text, callback_data=f"toggle_fav_{fatwa_id}"))

        from urllib.parse import quote
        report_msg = f"السلام عليكم ورحمة الله وبركاته\nأريد الابلاغ عن فتوى التي تحمل رقم: {fatwa.get('fatwa_number', fatwa_id)}"
        encoded_msg = quote(report_msg)
        report_url = f"https://t.me/abulharith_imad?text={encoded_msg}"

        action_buttons.append(InlineKeyboardButton("⚠️ إبلاغ", url=report_url))
        keyboard.append(action_buttons)

        if is_admin:
            keyboard.append([
                InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"),
                InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")
            ])

        if is_admin:
            keyboard.append([InlineKeyboardButton("📢 إرسال الفتوى", callback_data=f"broadcast_{fatwa_id}")])

        keyboard.append([
            InlineKeyboardButton("📋 نسخ النص", callback_data=f"copy_full_{fatwa_id}"),
            InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # تقسيم الرسالة
        message_parts = split_long_message(text)

        for i, part in enumerate(message_parts):
            is_last = (i == len(message_parts) - 1)
            await update.message.reply_text(
                part,
                reply_markup=reply_markup if is_last else None,
                disable_web_page_preview=True
            )

    except Exception as e:
        logger.error(f"Error sending fatwa message: {e}")
        await update.message.reply_text("❌ حدث خطأ أثناء عرض الفتوى.", reply_markup=create_main_keyboard())

async def view_fatwas_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة الفتاوى (صفحة التصفح)"""
    # هذا كان يستخدم سابقاً للعرض العام، يمكن توجيهه للبحث
    # أو عرض آخر 10
    from handlers.search import search_latest
    return await search_latest(update, context)

async def copy_fatwa_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نسخ الفتوى كاملة مع الروابط داخل النص"""
    from core.utils import split_long_message, format_full_fatwa_for_copy

    query = update.callback_query
    fatwa_id = int(query.data.split('_')[2])
    fatwa = db.get_fatwa(fatwa_id)

    if fatwa:
        # نص الفتوى مع روابط المصدر/الصوتية داخل الرسالة
        text = format_full_fatwa_for_copy(fatwa)

        # تقسيم الرسالة إذا كانت طويلة
        message_parts = split_long_message(text)
        source_chat = update.effective_chat
        is_private_chat = bool(source_chat and source_chat.type == "private")
        recipient_user = update.effective_user

        if is_private_chat:
            for part in message_parts:
                await query.message.reply_text(part)
            await query.answer("📋 تم إرسال نسخة الفتوى")
            return

        if not recipient_user or not recipient_user.id:
            await query.answer(
                "تعذر تحديد حسابك. اضغط الزر من حسابك الشخصي غير المجهول ثم أعد المحاولة.",
                show_alert=True,
            )
            return

        try:
            for part in message_parts:
                await context.bot.send_message(chat_id=recipient_user.id, text=part)
            await query.answer("📬 تم إرسال نسخة الفتوى إلى الخاص")
        except Exception as e:
            logger.warning(f"Failed to send copied fatwa {fatwa_id} to private chat {recipient_user.id}: {e}")
            bot_username = context.bot.username or "Fatwa_CMS_Bot"
            await query.answer(
                f"تعذر الإرسال إلى الخاص. افتح @{bot_username} أولاً ثم أعد المحاولة.",
                show_alert=True,
            )
    else:
        await query.answer("❌ خطأ: الفتوى غير موجودة")

async def broadcast_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال الفتوى للجهات المشتركة (أو للمدير فقط أثناء الصيانة)."""
    from core.utils import build_fatwa_preview_text

    query = update.callback_query
    await query.answer("⏳ جارٍ الإرسال... قد يستغرق ذلك بضع ثوانٍ.", cache_time=0)

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ غير مصرح لك.", show_alert=True)
        return

    fatwa_id = int(query.data.split('_')[1])
    fatwa = db.get_fatwa(fatwa_id)

    if not fatwa:
        await query.answer("❌ الفتوى غير موجودة.", show_alert=True)
        return

    maintenance_enabled = (bot_db.get_setting("maintenance_mode", "0") == "1")
    admin_id = int(update.effective_user.id)

    if maintenance_enabled:
        target_channels = []
        target_groups = []
        users = [admin_id]
    else:
        all_channels = bot_db.get_channels(status='active')
        users = list(dict.fromkeys(bot_db.get_active_user_ids()))
        target_channels = [ch for ch in all_channels if ch['type'] == 'channel']
        target_groups = [ch for ch in all_channels if ch['type'] in ['group', 'supergroup']]

    text_to_send, is_long = build_fatwa_preview_text(fatwa, max_length=3600)

    reply_markup = create_published_fatwa_keyboard(
        fatwa=fatwa,
        bot_username=context.bot.username,
        is_long=is_long,
    )

    if not (target_channels or target_groups or users):
        await query.answer("⚠️ لا توجد جهات إرسال نشطة حاليًا.", show_alert=True)
        try:
            preview_msg = await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=text_to_send,
                reply_markup=reply_markup,
            )
            _register_delivery_message(context, fatwa_id, update.effective_user.id, preview_msg.message_id)
            await query.message.reply_text("📬 تم إرسال معاينة لك لعدم وجود جهات مستقبلة حالياً.")
        except Exception as e:
            logger.debug(f"Failed to send self-preview fallback for fatwa {fatwa_id}: {e}")
        return

    report = {
        'channels': {'success': 0, 'fail': 0},
        'groups': {'success': 0, 'fail': 0},
        'subscribers': {'success': 0, 'fail': 0},
    }

    sent_to = set()

    async def send_to_list(targets, category_key, is_chat_dict=False):
        for target in targets:
            chat_id = target['chat_id'] if is_chat_dict else target
            if chat_id in sent_to:
                continue

            try:
                sent_msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=text_to_send,
                    reply_markup=reply_markup,
                )
                _register_delivery_message(context, fatwa_id, chat_id, sent_msg.message_id)
                sent_to.add(chat_id)
                report[category_key]['success'] += 1
            except Exception as e:
                report[category_key]['fail'] += 1
                err = str(e)
                if is_chat_dict and ("Forbidden" in err or "chat not found" in err.lower()):
                    bot_db.update_channel_status(chat_id, 'inactive')
                if not is_chat_dict:
                    lower_err = err.lower()
                    if (
                        chat_id != admin_id
                        and ("forbidden" in lower_err or "bot was blocked" in lower_err or "user is deactivated" in lower_err)
                    ):
                        bot_db.set_user_blocked(chat_id, True)

    await send_to_list(target_channels, 'channels', is_chat_dict=True)
    await send_to_list(target_groups, 'groups', is_chat_dict=True)
    await send_to_list(users, 'subscribers', is_chat_dict=False)

    approx_views = report['channels']['success'] + report['groups']['success']
    if approx_views > 0:
        db.increment_views_by(fatwa_id, approx_views)

    if not users:
        try:
            fallback_msg = await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=text_to_send,
                reply_markup=reply_markup,
            )
            _register_delivery_message(context, fatwa_id, update.effective_user.id, fallback_msg.message_id)
            sent_to.add(update.effective_user.id)
        except Exception as e:
            logger.debug(f"Failed to send admin fallback copy for fatwa {fatwa_id}: {e}")

    if maintenance_enabled:
        summary = (
            "🛠️ **وضع الصيانة مفعّل**\n\n"
            "تم إرسال الفتوى للمدير فقط.\n"
            f"✅ نجاح الإرسال: {report['subscribers']['success']}\n"
            f"❌ فشل الإرسال: {report['subscribers']['fail']}"
        )
    else:
        summary = (
            "✅ **تم إرسال الفتوى بنجاح**\n\n"
            f"📢 **القنوات:** {report['channels']['success']} (فشل: {report['channels']['fail']})\n"
            f"👥 **المجموعات:** {report['groups']['success']} (فشل: {report['groups']['fail']})\n"
            f"👤 **المشتركون:** {report['subscribers']['success']} (فشل: {report['subscribers']['fail']})"
        )

    await query.message.reply_text(
        summary,
        parse_mode='Markdown',
        reply_markup=_build_delivery_report_keyboard(fatwa_id),
    )


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

    # اختيار العالم (الخطوة 2)
    # استخدام دالة العرض المساعدة لتطبيق التفحيم
    return await show_scholars_step(update, context, page=0)

async def show_scholars_step(update_obj, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    _set_add_step(context, "scholar")

    # استخدام استعلام البحث إن وجد
    if search_query is None:
        search_query = context.user_data.get('scholar_search_query')

    scholars = db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = db.get_scholars_count(search_query=search_query)

    keyboard = []
    row = []
    for s_id, s_name in scholars: # Assuming scholars now returns (id, name) tuples
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
                [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]
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
        # The data is in format 'scholar_{id}'
        scholar_id = int(data.replace("scholar_", ""))

        # Fetch actual name from DB
        scholar_data = db.get_scholar_by_id(scholar_id)
        if scholar_data:
            scholar_name = scholar_data['name']
        else:
            # Fallback (should not happen if ID comes from list)
            scholar_name = "Unknown Scholar"

        context.user_data['new_fatwa']['scholar_name'] = scholar_name
        _set_add_step(context, "question")

        # Escape for display
        display_scholar = escape_markdown(scholar_name)

        await query.edit_message_text(
            f"✅ تم اختيار: {display_scholar}\n\n❓ **أرسل نص السؤال**:",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True),
            parse_mode='Markdown'
        )
        return STATE_QUESTION

async def handle_scholar_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة نص البحث عن العالم"""
    search_query = update.message.text.strip()
    # نحتاج لحفظ الاستعلام في context لدعم التفحيم داخل نتائج البحث
    context.user_data['scholar_search_query'] = search_query
    await show_scholars_step(update, context, page=0, search_query=search_query)
    return STATE_SCHOLAR

async def receive_scholar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # حالة استقبال اسم عالم جديد يدوياً
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

    # Any newly received text invalidates previous duplicate-check prompt state.
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
        await query.answer("\u26a0\ufe0f \u0623\u0631\u0633\u0644 \u0646\u0635 \u0627\u0644\u0641\u062a\u0648\u0649 \u0623\u0648\u0644\u0627\u064b.", show_alert=True)
        return STATE_FATWA_TEXT

    answer_text = "\n".join(parts).strip()
    duplicates = db.find_fatwas_by_exact_answer(answer_text, limit=3)

    if duplicates:
        first_dup = duplicates[0]
        fatwa_number = first_dup.get('fatwa_number') or first_dup.get('id')
        title = first_dup.get('title') or "\u0628\u062f\u0648\u0646 \u0639\u0646\u0648\u0627\u0646"
        extra_count = max(0, len(duplicates) - 1)
        extra_line = (
            f"\n\u2022 \u064a\u0648\u062c\u062f \u0623\u064a\u0636\u064b\u0627 {extra_count} \u0646\u062a\u064a\u062c\u0629 \u0645\u0634\u0627\u0628\u0647\u0629."
            if extra_count
            else ""
        )

        context.user_data[_PENDING_DUPLICATE_ANSWER_KEY] = answer_text
        context.user_data[_PENDING_DUPLICATE_MATCHES_KEY] = duplicates

        await query.edit_message_text(
            "\u26a0\ufe0f \u062a\u0646\u0628\u064a\u0647: \u0647\u0630\u0647 \u0627\u0644\u0641\u062a\u0648\u0649 \u0645\u0648\u062c\u0648\u062f\u0629 \u0645\u0646 \u0642\u0628\u0644 \u0628\u0646\u0641\u0633 \u0627\u0644\u0646\u0635.\n\n"
            f"\u2022 \u0631\u0642\u0645 \u0627\u0644\u0641\u062a\u0648\u0649: {fatwa_number}\n"
            f"\u2022 \u0627\u0644\u0639\u0646\u0648\u0627\u0646: {title}"
            f"{extra_line}\n\n"
            "\u0627\u062e\u062a\u0631 \u0627\u0644\u0625\u062c\u0631\u0627\u0621:",
            reply_markup=_duplicate_fatwa_choice_keyboard()
        )
        return STATE_FATWA_TEXT

    return await _continue_add_after_fatwa_text(update, context, answer_text)


async def _continue_add_after_fatwa_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    answer_text: str,
):
    context.user_data['new_fatwa']['answer'] = answer_text
    context.user_data.pop('fatwa_text_parts', None)
    context.user_data.pop(_PENDING_DUPLICATE_ANSWER_KEY, None)
    context.user_data.pop(_PENDING_DUPLICATE_MATCHES_KEY, None)

    # Start with fiqh categories
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
        context.user_data.pop(_PENDING_DUPLICATE_ANSWER_KEY, None)
        context.user_data.pop(_PENDING_DUPLICATE_MATCHES_KEY, None)

        from handlers.admin import admin_panel
        await admin_panel(update, context)
        return ConversationHandler.END

    answer_text = context.user_data.get(_PENDING_DUPLICATE_ANSWER_KEY)
    if not answer_text:
        parts = context.user_data.get('fatwa_text_parts', [])
        if not parts:
            await query.answer("\u26a0\ufe0f \u0623\u0631\u0633\u0644 \u0646\u0635 \u0627\u0644\u0641\u062a\u0648\u0649 \u0623\u0648\u0644\u0627\u064b.", show_alert=True)
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
    category_type = 'fiqh' if slot == 1 else 'topic'
    cats = db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=category_type)
    total_count = db.get_categories_count(search_query=search_query, category_type=category_type)

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

    # أزرار البحث والإضافة في صف واحد أو صفين حسب الرغبة
    # المطلوب: "اضف زر البحث ... وزر تصنيف جديد"
    action_row = [
        InlineKeyboardButton("🔍 بحث تصنيف", callback_data="search_cat_add"),
        InlineKeyboardButton("➕ تصنيف جديد", callback_data="add_new_category")
    ]
    keyboard.append(action_row)

    # Allow skipping slots
    if slot == 1:
        keyboard.append([InlineKeyboardButton("⏭️ تخطي التصنيف الفقهي", callback_data="skip_fiqh_categories")])
    if slot == 2:
        keyboard.append([InlineKeyboardButton("⏭️ تخطي التصنيف الموضوعي", callback_data="skip_topic_categories")])

    keyboard.append([
        InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"),
        InlineKeyboardButton("❌ إلغاء", callback_data="cancel")
    ])

    label = "الفقهي" if context.user_data.get('taxonomy_slot', 1) == 1 else "الموضوعي"

    # رسالة ترحيبية أو انتقالية
    msg_text = f"✅ تم حفظ نص الفتوى.\n\n🏷️ **اختر {label}** (صفحة {page+1}):"
    reply_markup = InlineKeyboardMarkup(keyboard)

    await _send_add_prompt(update, msg_text, reply_markup=reply_markup)

    return STATE_CATEGORIES

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "back_step":
        return await handle_back_step(update, context)
    if data == "cancel":
        return await cancel_operation(update, context)
    if data == "back_main":
        return await back_to_main(update, context)

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
        return STATE_CATEGORY_1 if slot == 1 else STATE_CATEGORY_2
    elif data == "skip_topic_categories":
        # Skip الموضوعي slot and continue to source
        await ask_source(update, context)
        return STATE_SOURCE
    elif data == "skip_fiqh_categories":
        # Skip الفقهي slot and move to الموضوعي categories
        context.user_data['taxonomy_slot'] = 2
        return await show_categories_step(update, context, page=0)
    elif data == "add_new_category":
        await query.edit_message_text(
            "🏷️ أرسل اسم التصنيف الجديد:",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_CATEGORY_1 if context.user_data.get('taxonomy_slot', 1) == 1 else STATE_CATEGORY_2
    elif data.startswith("cat_page_"):
        page = int(data.split('_')[-1])
        await show_categories_step(update, context, page)
        return STATE_CATEGORY_1 if context.user_data.get('taxonomy_slot', 1) == 1 else STATE_CATEGORY_2
    elif data.startswith("category_"):
        cat_id = int(data.split('_')[-1])
        # Save Category
        if 'new_fatwa' not in context.user_data:
            context.user_data['new_fatwa'] = {}
        if 'classifications' not in context.user_data['new_fatwa']:
            context.user_data['new_fatwa']['classifications'] = []

        # نتحقق إذا كان التصنيف موجوداً بالفعل لهذا السلوت (تحديث)
        # لكن في الإضافة الجديدة عادة لا يوجد.
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
    return STATE_CATEGORY_1 if context.user_data.get('taxonomy_slot', 1) == 1 else STATE_CATEGORY_2

async def receive_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_name = update.message.text
    slot = context.user_data.get('taxonomy_slot', 1)
    cat_type = 'fiqh' if slot == 1 else 'topic'
    cat_id = db.add_category(cat_name, category_type=cat_type)
    if not cat_id:
        # Fetch existing
        cats = db.get_categories()
        for cid, name in cats:
            if name == cat_name:
                cat_id = cid
                break

    if cat_id:
        context.user_data['current_cat_id'] = cat_id

    label = "الفقهي" if slot == 1 else "الموضوعي"
    await update.message.reply_text(f"✅ تم إضافة التصنيف {label}: {cat_name}")
    return await show_topics_step(update, context, cat_id)


async def show_topics_step(update_obj, context, cat_id, page=0, search_query=None):
    """عرض قائمة المواضيع المتاحة للتصنيف المختار - دعم الاختيار المتعدد"""
    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE

    cat_row = db.get_category(cat_id)
    cat_name = cat_row['name'] if cat_row else "غير محدد"

    if search_query is None:
        search_query = context.user_data.get(f'topic_search_query_{cat_id}')
    else:
        context.user_data[f'topic_search_query_{cat_id}'] = search_query

    topics = db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
    total_count = db.get_topics_count(cat_id, search_query=search_query)

    slot = context.user_data.get('taxonomy_slot', 1)
    _set_add_step(context, f"topics_{slot}")
    label = "الفقهي" if slot == 1 else "الموضوعي"

    # جلب المواضيع المختارة حالياً لهذا السلوت
    selected_topics = context.user_data.get(f'selected_topics_slot_{slot}', [])

    selected_topic_names = []
    for tid in selected_topics:
        topic_row = db.get_topic(tid)
        if topic_row and topic_row.get("name"):
            selected_topic_names.append(topic_row["name"])
    if selected_topic_names:
        selected_topics_text = ", ".join(escape_markdown(name) for name in selected_topic_names)
    else:
        selected_topics_text = "لا يوجد"

    # ... (rest of keyboard logic remains same, just updating label)

    keyboard = []
    row = []
    for tid, name in topics:
        text = f"\u2705 {name}" if tid in selected_topics else name
        row.append(InlineKeyboardButton(text, callback_data=f"toggle_topic_{tid}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"topic_page_{page-1}"))

    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"topic_page_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    action_row = [
        InlineKeyboardButton("🔍 بحث موضوع", callback_data="search_topic"),
        InlineKeyboardButton("➕ موضوع جديد", callback_data="add_new_topic")
    ]
    keyboard.append(action_row)

    keyboard.append([InlineKeyboardButton("✅ إتمام", callback_data="done_topics")])
    keyboard.append([
        InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"),
        InlineKeyboardButton("❌ إلغاء", callback_data="cancel")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    msg_text = (
        f"🏷️ التصنيف {label}: **{escape_markdown(cat_name)}**\n"
        f"✅ **المواضيع المختارة:** {selected_topics_text}\n"
        "📑 **اختر المواضيع** (يمكنك اختيار أكثر من موضوع):\n\n"
        "(اضغط على 'إتمام' عند الانتهاء)"
    )

    await _send_add_prompt(update_obj, msg_text, reply_markup=reply_markup, parse_mode='Markdown')

    return STATE_TOPICS

async def handle_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    slot = context.user_data.get('taxonomy_slot', 1)
    cat_id = context.user_data.get('current_cat_id')

    if data == "back_step":
        return await handle_back_step(update, context)
    if data == "cancel":
        return await cancel_operation(update, context)
    if data == "back_main":
        return await back_to_main(update, context)

    if data == "search_topic":
        await query.edit_message_text(
            "🔍 أرسل اسم الموضوع للبحث عنه:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء البحث", callback_data="topic_search_cancel")],
                [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
            ])
        )
        return STATE_ADD_FATWA_TOPIC_SEARCH
    elif data == "topic_search_cancel":
        if cat_id:
            context.user_data.pop(f'topic_search_query_{cat_id}', None)
            await show_topics_step(query, context, cat_id, page=0, search_query=None)
            return STATE_TOPICS
        return await show_categories_step(update, context, page=0)

    elif data == "add_new_topic":
        await query.edit_message_text(
            "📑 أرسل اسم الموضوع الجديد:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء", callback_data="topic_page_0")],
                [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
            ])
        )
        return STATE_TOPICS # MessageHandler handles it

    elif data.startswith("topic_page_"):
        page = int(data.split('_')[-1])
        await show_topics_step(query, context, cat_id, page)
        return STATE_TOPICS

    elif data.startswith("toggle_topic_"):
        topic_id = int(data.split('_')[-1])
        key = f'selected_topics_slot_{slot}'
        if key not in context.user_data:
            context.user_data[key] = []

        if topic_id in context.user_data[key]:
            context.user_data[key].remove(topic_id)
        else:
            context.user_data[key].append(topic_id)

        # تحديث القائمة
        await show_topics_step(query, context, cat_id)
        return STATE_TOPICS

    elif data == "done_topics":
        slot = context.user_data.get('taxonomy_slot', 1)
        topic_ids = context.user_data.get(f'selected_topics_slot_{slot}', [])
        return await save_classification_and_continue(query, context, topic_ids)

    elif data == "skip_topics":
        return await save_classification_and_continue(query, context, [])

async def handle_back_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    prev_step = _pop_add_step(context)
    if not prev_step:
        return await back_to_main(update, context)

    context.user_data['add_step'] = prev_step

    if prev_step == "title":
        await _send_add_prompt(
            update,
            "📝 **إضافة فتوى جديدة**\n\n📌 أرسل **عنوان الفتوى**:",
            _add_flow_nav_keyboard(include_back_step=False)
        )
        return STATE_TITLE

    if prev_step == "scholar":
        return await show_scholars_step(update, context, page=0)

    if prev_step == "question":
        await _send_add_prompt(
            update,
            "❓ **أرسل نص السؤال**:",
            _add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_QUESTION

    if prev_step == "answer":
        context.user_data.pop('fatwa_text_parts', None)
        context.user_data.get('new_fatwa', {}).pop('answer', None)
        context.user_data.pop(_PENDING_DUPLICATE_ANSWER_KEY, None)
        context.user_data.pop(_PENDING_DUPLICATE_MATCHES_KEY, None)
        await _send_add_prompt(
            update,
            "📄 **أرسل نص الفتوى**:",
            _fatwa_text_nav_keyboard()
        )
        return STATE_FATWA_TEXT

    if prev_step.startswith("category_"):
        slot = int(prev_step.split('_')[1])
        context.user_data['taxonomy_slot'] = slot
        return await show_categories_step(update, context, page=0)

    if prev_step.startswith("topics_"):
        slot = int(prev_step.split('_')[1])
        context.user_data['taxonomy_slot'] = slot
        cat_id = context.user_data.get('current_cat_id')
        if not cat_id:
            return await show_categories_step(update, context, page=0)
        return await show_topics_step(update, context, cat_id, page=0)

    if prev_step == "source":
        await ask_source(update, context)
        return STATE_SOURCE

    if prev_step == "source_title":
        await _send_add_prompt(
            update,
            "🎙️ أرسل **عنوان المصدر** (مثل: اسم الشريط أو الحلقة):",
            _source_title_keyboard()
        )
        return STATE_SOURCE_TITLE

    if prev_step == "source_url":
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")],
            [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ])
        await _send_add_prompt(
            update,
            "🔗 أرسل **رابط عنوان المصدر** (إن وجد) أو اضغط تخطي:",
            reply_markup
        )
        return STATE_SOURCE_URL

    if prev_step == "audio":
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ تخطي", callback_data="skip_audio")],
            [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ])
        await _send_add_prompt(
            update,
            "🔊 أرسل **رابط الصوتية** (إن وجد) أو اضغط تخطي:",
            reply_markup
        )
        return STATE_AUDIO

    return await back_to_main(update, context)

async def save_classification_and_continue(update_obj, context, topic_ids):
    cat_id = context.user_data.get('current_cat_id')
    slot = context.user_data.get('taxonomy_slot', 1)

    # تحديث أو إضافة التصنيف لهذا السلوت
    updated = False
    for cls in context.user_data['new_fatwa']['classifications']:
        # تحديث فقط إذا كان نفس السلوت ونفس التصنيف (تحديث المواضيع)
        if cls['slot_index'] == slot and cls['category_id'] == cat_id:
            cls['topic_ids'] = topic_ids
            updated = True
            break

    if not updated:
        # إضافة تصنيف جديد للسلوت (قد يكون هناك عدة تصنيفات لنفس السلوت)
        context.user_data['new_fatwa']['classifications'].append({
            'category_id': cat_id,
            'topic_ids': topic_ids,
            'slot_index': slot
        })

    # تنظيف الذاكرة المؤقتة للمواضيع المختارة لهذا السلوت
    context.user_data.pop(f'selected_topics_slot_{slot}', None)

    # تنظيف الذاكرة المؤقتة للمواضيع المختارة
    context.user_data.pop('selected_topics', None)

    if slot == 1:
        # Move to الموضوعي categories after finishing الفقهي
        context.user_data['taxonomy_slot'] = 2
        return await show_categories_step(update_obj, context)

    await ask_source(update_obj, context)
    return STATE_SOURCE

async def show_sources_step(update_obj, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    _set_add_step(context, "source")
    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE

    sources = db.get_sources(limit=ITEMS_PER_PAGE, offset=offset)
    total_count = db.get_sources_count()

    keyboard = []
    if sources:
        row = []
        for source_id, name in sources:
            row.append(InlineKeyboardButton(f"\U0001f4da {name}", callback_data=f"pick_source_{source_id}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
    else:
        keyboard.append([InlineKeyboardButton("❌ لا توجد مصادر محفوظة", callback_data="source_manual")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"source_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"source_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("✍️ كتابة مصدر جديد", callback_data="source_manual")])
    keyboard.append([
        InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"),
        InlineKeyboardButton("❌ إلغاء", callback_data="cancel")
    ])

    msg = "📚 اختر مصدرًا من القائمة أو اكتب مصدرًا جديدًا:"
    reply_markup = InlineKeyboardMarkup(keyboard)

    if isinstance(update_obj, Update) and update_obj.callback_query:
        await update_obj.callback_query.edit_message_text(msg, reply_markup=reply_markup)
    elif hasattr(update_obj, 'edit_message_text'):
        await update_obj.edit_message_text(msg, reply_markup=reply_markup)
    elif isinstance(update_obj, Update) and update_obj.message:
        await update_obj.message.reply_text(msg, reply_markup=reply_markup)
    elif hasattr(update_obj, 'reply_text'):
        await update_obj.reply_text(msg, reply_markup=reply_markup)

    return STATE_SOURCE


async def handle_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("source_page_"):
        page = int(data.split('_')[-1])
        return await show_sources_step(update, context, page=page)

    if data == "source_manual":
        await query.edit_message_text(
            "✍️ أرسل اسم المصدر الجديد:",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_SOURCE

    if data.startswith("pick_source_"):
        source_id = int(data.split('_')[-1])
        source = db.get_source(source_id)
        if not source:
            await query.edit_message_text(
                "❌ المصدر غير موجود.",
                reply_markup=_add_flow_nav_keyboard(include_back_step=True)
            )
            return STATE_SOURCE

        context.user_data['new_fatwa']['source_name'] = source['name']
        _set_add_step(context, "source_title")
        await query.edit_message_text(
            "🎙️ أرسل **عنوان المصدر** (مثل: اسم الشريط أو الحلقة):",
            reply_markup=_source_title_keyboard(),
            parse_mode='Markdown'
        )
        return STATE_SOURCE_TITLE

    return STATE_SOURCE

async def ask_source(update_obj, context: ContextTypes.DEFAULT_TYPE):
    return await show_sources_step(update_obj, context, page=0)

async def receive_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    text = update.message.text
    if contains_url(text):
        await update.message.reply_text(
            "⚠️ اسم المصدر لا يقبل الروابط.",
            reply_markup=_add_flow_nav_keyboard(include_back_step=True)
        )
        return STATE_SOURCE

    context.user_data['new_fatwa']['source_name'] = text
    _set_add_step(context, "source_title")
    await update.message.reply_text(
        "🎙️ أرسل **عنوان المصدر** (مثل: اسم الشريط أو الحلقة):",
        reply_markup=_source_title_keyboard()
    )
    return STATE_SOURCE_TITLE

async def receive_source_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import contains_url
    text = update.message.text
    if contains_url(text):
         await update.message.reply_text(
             "⚠️ عنوان المصدر لا يقبل الروابط.",
             reply_markup=_source_title_keyboard()
         )
         return STATE_SOURCE_TITLE

    context.user_data['new_fatwa']['source_title'] = text
    _set_add_step(context, "source_url")
    await update.message.reply_text(
        "🔗 أرسل **رابط عنوان المصدر** (إن وجد) أو اضغط تخطي:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")],
            [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ])
    )
    return STATE_SOURCE_URL

async def skip_source_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['new_fatwa']['source_title'] = ""
    _set_add_step(context, "source_url")
    await query.edit_message_text(
        "🔗 أرسل **رابط عنوان المصدر** (إن وجد) أو اضغط تخطي:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭️ تخطي", callback_data="skip_source_url")],
            [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
        ])
    )
    return STATE_SOURCE_URL

async def receive_source_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "skip_source_url":
            pass # Already None or empty
    else:
        text = update.message.text
        from core.utils import is_valid_url
        if not is_valid_url(text):
             await update.message.reply_text("⚠️ الرابط غير صحيح. الرجاء إرسال رابط صالح (http/https).")
             return STATE_SOURCE_URL

        context.user_data['new_fatwa']['source_url'] = text

    _set_add_step(context, "audio")
    msg = "🔊 أرسل **رابط الصوتية** (إن وجد) أو اضغط تخطي:"
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭️ تخطي", callback_data="skip_audio")],
        [InlineKeyboardButton("🔙 رجوع خطوة", callback_data="back_step"), InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])

    if query:
        await query.edit_message_text(msg, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup)
    return STATE_AUDIO

async def receive_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from core.utils import split_long_message
    query = update.callback_query
    if query:
        await query.answer()
        if query.data == "skip_audio":
            pass
    else:
        text = update.message.text
        if text != '/skip':
            from core.utils import is_valid_url
            if not is_valid_url(text):
                await safe_reply_text(update.message, "⚠️ رابط الصوتية غير صحيح. الرجاء إرسال رابط صالح.")
                return STATE_AUDIO

            context.user_data['new_fatwa']['audio_url'] = text

    # الحفظ النهائي كمسودة
    fatwa_data = context.user_data['new_fatwa']
    fatwa_data['status'] = 'draft' # Explicit draft
    fatwa_id = db.add_fatwa(fatwa_data)

    # عرض المعاينة النهائية الكاملة
    fatwa = db.get_fatwa(fatwa_id)
    text = "✅ تم حفظ الفتوى بنجاح!\n\n-- معاينة الفتوى --\n\n"
    text += format_fatwa_content(fatwa)

    keyboard = []
    # صف النشر
    keyboard.append([InlineKeyboardButton("📢 نشر الفتوى", callback_data=f"publish_{fatwa_id}")])
    # صف الإدارة
    admin_row = [
        InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"),
        InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")
    ]
    keyboard.append(admin_row)
    # صف عام
    keyboard.append([
        InlineKeyboardButton("📋 نسخ نص الفتوى", callback_data=f"copy_full_{fatwa_id}"),
        InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")
    ])
    keyboard.append([InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    message_parts = split_long_message(text)
    if query:
        await safe_edit_message_text(
            query,
            message_parts[0],
            reply_markup=reply_markup if len(message_parts) == 1 else None,
            disable_web_page_preview=True
        )
        for i, part in enumerate(message_parts[1:], 1):
            is_last = (i == len(message_parts) - 1)
            await safe_reply_text(
                query.message,
                part,
                reply_markup=reply_markup if is_last else None,
                disable_web_page_preview=True
            )
    else:
        await safe_reply_text(
            update.message,
            message_parts[0],
            reply_markup=reply_markup if len(message_parts) == 1 else None,
            disable_web_page_preview=True
        )
        for i, part in enumerate(message_parts[1:], 1):
            is_last = (i == len(message_parts) - 1)
            await safe_reply_text(
                update.message,
                part,
                reply_markup=reply_markup if is_last else None,
                disable_web_page_preview=True
            )

    return ConversationHandler.END


async def receive_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استقبال اسم موضوع جديد يدوياً"""
    topic_name = update.message.text.strip()
    cat_id = context.user_data.get('current_cat_id')

    if not cat_id:
        await update.message.reply_text("❌ خطأ: لم يتم تحديد التصنيف.")
        return STATE_TOPICS

    # إضافة الموضوع الجديد
    topic_id = db.add_topic(topic_name, cat_id)

    if topic_id:
        # إضافته للمواضيع المختارة
        if 'selected_topics' not in context.user_data:
            context.user_data['selected_topics'] = []
        if topic_id not in context.user_data['selected_topics']:
            context.user_data['selected_topics'].append(topic_id)

        await update.message.reply_text(f"✅ تم إضافة الموضوع: {topic_name}")
    else:
        await update.message.reply_text("❌ حدث خطأ في إضافة الموضوع.")

    # إعادة عرض قائمة المواضيع
    return await show_topics_step(update, context, cat_id)

async def handle_topic_search_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة البحث عن موضوع أثناء الإضافة"""
    search_query = update.message.text.strip()
    cat_id = context.user_data.get('current_cat_id')

    if not cat_id:
        await update.message.reply_text("❌ خطأ: لم يتم تحديد التصنيف.")
        return STATE_TOPICS

    context.user_data[f'topic_search_query_{cat_id}'] = search_query
    await show_topics_step(update, context, cat_id, page=0, search_query=search_query)
    return STATE_TOPICS

async def handle_taxonomy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة قائمة التصنيف (إذا كانت مطلوبة)"""
    query = update.callback_query
    await query.answer()
    # يمكن توسيع هذه الوظيفة حسب الحاجة
    return STATE_TAXONOMY_MENU


# ==================== عمليات النشر والحذف ====================

async def delete_fatwa_from_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("جاري الحذف من الكل...", cache_time=0)

    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ غير مصرح لك.", show_alert=True)
        return

    try:
        fatwa_id = int(query.data.split('_')[-1])
    except (TypeError, ValueError):
        await query.answer("❌ طلب غير صالح.", show_alert=True)
        return

    app = getattr(context, "application", None)
    if not app:
        await query.message.reply_text("❌ تعذر الوصول إلى بيانات الإرسال.")
        return

    store = app.bot_data.setdefault(_DELIVERY_LOG_KEY, {})
    fatwa_store = store.get(str(fatwa_id), {})

    if not fatwa_store:
        await query.message.reply_text(
            "ℹ️ لا توجد رسائل محفوظة لهذه الفتوى للحذف من الكل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]])
        )
        return

    deleted_count = 0
    failed_count = 0
    remaining: dict[str, list[int]] = {}

    for chat_id_raw, msg_ids in fatwa_store.items():
        try:
            chat_id = int(chat_id_raw)
        except (TypeError, ValueError):
            failed_count += len(msg_ids)
            continue

        remaining_ids: list[int] = []
        for msg_id in msg_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=int(msg_id))
                deleted_count += 1
            except Exception as e:
                err = str(e).lower()
                # لا نعيد المحاولة على رسائل لا يمكن حذفها أصلاً.
                if (
                    "message to delete not found" in err
                    or "message can't be deleted" in err
                    or "message_id_invalid" in err
                ):
                    failed_count += 1
                    continue
                failed_count += 1
                remaining_ids.append(int(msg_id))

        if remaining_ids:
            remaining[chat_id_raw] = remaining_ids

    if remaining:
        store[str(fatwa_id)] = remaining
    else:
        store.pop(str(fatwa_id), None)

    await query.message.reply_text(
        "✅ تم تنفيذ الحذف من الكل.\n"
        f"🗑️ رسائل حُذفت: {deleted_count}\n"
        f"⚠️ لم تُحذف: {failed_count}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]])
    )


async def publish_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("للإدارة فقط")
        return

    fatwa_id = int(query.data.split('_')[1])
    context_view_data = _resolve_view_context_data(context, fatwa_id, query.data)
    db.update_fatwa(fatwa_id, {'status': 'published'})
    await query.answer("✅ تم النشر!")

    published_fatwa = db.get_fatwa(fatwa_id) or {}

    suffix = _extract_context_suffix(query.data, fatwa_id) or ""
    if suffix.startswith("drafts"):
        parts = suffix.split("_")
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        from handlers.admin import show_admin_drafts
        return await show_admin_drafts(update, context, page=page)

    # تحديث العرض
    fatwa = published_fatwa or db.get_fatwa(fatwa_id)
    text = format_fatwa_content(fatwa)
    # أعد رسم الأزرار بدون زر النشر
    # أعد رسم الأزرار بدون زر النشر
    keyboard = []

    # صف 1: تعديل + حذف
    keyboard.append([
        InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"),
        InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")
    ])

    # صف 2: ارسال
    keyboard.append([InlineKeyboardButton("📢 إرسال الفتوى", callback_data=f"broadcast_{fatwa_id}")])

    if context_view_data:
        from types import SimpleNamespace
        back_btn = _build_view_back_button(SimpleNamespace(data=context_view_data), context)
    else:
        back_btn = InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")

    # صف 3: نسخ + رجوع
    keyboard.append([
        InlineKeyboardButton("📋 نسخ النص", callback_data=f"copy_full_{fatwa_id}"),
        back_btn
    ])
    if back_btn.callback_data != "back_main":
        keyboard.append([InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_fatwa_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    fatwa_id = int(query.data.split('_')[2])
    context_view_data = _resolve_view_context_data(context, fatwa_id, query.data)
    cancel_cb = context_view_data or f"view_{fatwa_id}"
    suffix = _extract_context_suffix(context_view_data or "", fatwa_id)
    delete_cb = f"delete_final_{fatwa_id}"
    if suffix:
        delete_cb = f"{delete_cb}_{suffix}"

    await query.edit_message_text(
        "⚠️ **هل أنت متأكد من الحذف النهائي؟**",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ نعم، احذف", callback_data=delete_cb)],
            [InlineKeyboardButton("❌ إلغاء", callback_data=cancel_cb)]
        ]),
        parse_mode='Markdown'
    )

async def delete_fatwa_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    fatwa_id = int(query.data.split('_')[2])
    db.delete_fatwa(fatwa_id)
    bot_db.remove_favorites_for_fatwa(fatwa_id)
    await query.answer("🗑️ تم الحذف")
    context_view_data = _resolve_view_context_data(context, fatwa_id, query.data)
    if context_view_data:
        from types import SimpleNamespace
        back_btn = _build_view_back_button(SimpleNamespace(data=context_view_data), context)
        await query.edit_message_text("✅ تم حذف الفتوى.", reply_markup=InlineKeyboardMarkup([[back_btn]]))
    else:
        await query.edit_message_text("✅ تم حذف الفتوى.", reply_markup=create_main_keyboard(True))

# ==================== تعريف المحادثة ====================

add_fatwa_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_add_fatwa, pattern='^add_fatwa$')],
    states={
        STATE_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title)],
        STATE_SCHOLAR: [
            CallbackQueryHandler(handle_scholar_selection),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scholar)
        ],
        STATE_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_question)],
        STATE_FATWA_TEXT: [
            CallbackQueryHandler(confirm_fatwa_text, pattern='^confirm_fatwa_text$'),
            CallbackQueryHandler(handle_duplicate_fatwa_choice, pattern='^dup_(cancel|continue)_add$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_fatwa_text)
        ],

        # التصنيف
        STATE_CATEGORIES: [
            CallbackQueryHandler(handle_category_selection),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category)
        ],
        STATE_CATEGORY_1: [
            CallbackQueryHandler(handle_category_selection),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category)
        ],
        STATE_CATEGORY_2: [
            CallbackQueryHandler(handle_category_selection),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_category)
        ],
        STATE_TOPICS: [
            CallbackQueryHandler(handle_topic_selection),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_topic)
        ],
        STATE_TAXONOMY_MENU: [
            CallbackQueryHandler(handle_taxonomy_menu)
        ],

        STATE_SOURCE: [
            CallbackQueryHandler(handle_source_selection, pattern='^(pick_source_|source_page_|source_manual$)'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source)
        ],
        STATE_SOURCE_TITLE: [
            CallbackQueryHandler(skip_source_title, pattern='^skip_source_title$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source_title)
        ],
        STATE_SOURCE_URL: [
            CallbackQueryHandler(receive_source_url, pattern='^skip_source_url$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_source_url)
        ],
        STATE_AUDIO: [
            CallbackQueryHandler(receive_audio, pattern='^skip_audio$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_audio),
            CommandHandler('skip', receive_audio)
        ],

        # حالات البحث المتقدم أثناء الإضافة
        STATE_ADD_FATWA_SCHOLAR_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_scholar_search_add),
            CallbackQueryHandler(handle_scholar_selection, pattern='^scholar_page_'),
            CallbackQueryHandler(handle_scholar_selection, pattern='^scholar_search_cancel$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        STATE_ADD_FATWA_CAT_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_category_search_add),
            CallbackQueryHandler(handle_category_selection, pattern='^cat_page_'),
            CallbackQueryHandler(handle_category_selection, pattern='^cat_search_cancel$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
        STATE_ADD_FATWA_TOPIC_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic_search_add),
            CallbackQueryHandler(handle_topic_selection, pattern='^topic_page_'),
            CallbackQueryHandler(handle_topic_selection, pattern='^topic_search_cancel$'),
            CallbackQueryHandler(back_to_main, pattern='^back_main$')
        ],
    },
    fallbacks=[
        CallbackQueryHandler(handle_back_step, pattern='^back_step$'),
        CallbackQueryHandler(start_refresh, pattern='^start_refresh$'),
        CallbackQueryHandler(back_to_main, pattern='^back_main$'),
        CallbackQueryHandler(cancel_operation, pattern='^cancel$'),
        CommandHandler('cancel', cancel_operation)
    ]
)

# Edit logic would go here similarly (abbreviated for size constraints)
# ==================== تعديل الفتوى ====================

def _format_edit_current_value(value, limit: int = 900) -> str:
    if value is None:
        return "— لا يوجد —"
    text = str(value).strip()
    if not text:
        return "— لا يوجد —"
    if len(text) > limit:
        return text[:limit].rstrip() + "\n..."
    return text


def _pair_buttons(buttons: list[InlineKeyboardButton]) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])
    return rows


def _format_edit_slot_summary(fatwa: dict, slot: int) -> str:
    classifications = [c for c in fatwa.get('classifications', []) if c.get('slot_index') == slot]
    if not classifications:
        return "لا توجد تصنيفات مختارة لهذا النوع."

    lines = []
    for idx, cls in enumerate(classifications, 1):
        cat_name = cls.get('category_name') or "غير محدد"
        topic_names = cls.get('topic_names') or []
        topics_text = "، ".join(topic_names) if topic_names else "بدون مواضيع"
        lines.append(f"{idx}. {cat_name}\n   المواضيع: {topics_text}")
    return "\n\n".join(lines)


def _build_edit_field_prompt(fatwa: dict, field: str, back_cb: str) -> tuple[str, InlineKeyboardMarkup]:
    labels = {
        "title": "العنوان",
        "scholar_name": "العالم",
        "question": "السؤال",
        "answer": "النص",
        "source_name": "المصدر",
        "source_title": "عنوان المصدر",
        "source_url": "رابط المصدر",
        "audio_url": "الرابط الصوتي",
    }
    prompts = {
        "title": "أرسل العنوان الجديد:",
        "scholar_name": "أرسل اسم العالم الجديد:",
        "question": "أرسل نص السؤال الجديد:",
        "answer": "أرسل نص الفتوى الجديد:",
        "source_name": "أرسل اسم المصدر الجديد:",
        "source_title": "أرسل عنوان المصدر الجديد:",
        "source_url": "أرسل رابط المصدر الجديد:",
        "audio_url": "أرسل الرابط الصوتي الجديد:",
    }
    clear_actions = {
        "source_name": "edit_clear_source_name",
        "source_title": "edit_clear_source_title",
        "source_url": "edit_clear_source_url",
        "audio_url": "edit_clear_audio_url",
    }

    current_value = _format_edit_current_value(fatwa.get(field))
    label = labels.get(field, "الحقل")
    prompt = prompts.get(field, "أرسل القيمة الجديدة:")

    text = (
        f"✏️ تعديل {label}\n\n"
        f"القيمة الحالية:\n{current_value}\n\n"
        f"{prompt}"
    )

    keyboard = []
    clear_cb = clear_actions.get(field)
    if clear_cb:
        keyboard.append([InlineKeyboardButton("🗑️ حذف الحالي", callback_data=clear_cb)])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=back_cb)])
    return text, InlineKeyboardMarkup(keyboard)


async def _show_edit_scholar_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query
    fatwa_id = context.user_data.get('edit_fatwa_id')
    fatwa = db.get_fatwa(fatwa_id) if fatwa_id else None
    if not fatwa:
        await query.answer("❌ تعذر تحميل الفتوى.", show_alert=True)
        return STATE_EDIT_MENU

    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE
    scholars = db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset)
    total_count = db.get_scholars_count()
    current_scholar = str(fatwa.get('scholar_name') or "").strip()

    keyboard = []
    row = []
    for sch_id, sch_name in scholars:
        label = f"✅ {sch_name}" if current_scholar and sch_name == current_scholar else sch_name
        row.append(InlineKeyboardButton(label, callback_data=f"edit_pick_scholar_{sch_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"edit_sch_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav.append(InlineKeyboardButton("➡️ التالي", callback_data=f"edit_sch_page_{page+1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("➕ إضافة عالم جديد", callback_data="edit_add_new_scholar")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=f"edit_fatwa_{fatwa_id}")])

    current_text = _format_edit_current_value(current_scholar, limit=200)
    text = (
        "👤 تعديل العالم\n\n"
        f"العالم الحالي: {current_text}\n\n"
        "اختر عالمًا من القائمة أو أضف عالمًا جديدًا:"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return STATE_EDIT_MENU


async def start_edit_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE, fatwa_id: int = None):
    """بدء عملية التعديل: عرض قائمة الحقول"""
    query = update.callback_query
    if query:
        await query.answer()

        if query.data and query.data.startswith('edit_fatwa_'):
            try:
                # هذا هو المصدر الموثوق عند بدء محادثة جديدة
                import re
                match = re.match(r'^edit_fatwa_(\d+)(?:_(.+))?$', query.data)
                extracted_id = int(match.group(1)) if match else int(query.data.split('_')[-1])
                if fatwa_id is None:
                    fatwa_id = extracted_id

                if match and match.group(2):
                    context.user_data['edit_view_context'] = f"view_{extracted_id}_{match.group(2)}"
                else:
                    context.user_data.pop('edit_view_context', None)
            except ValueError:
                pass

    if fatwa_id is None:
        # Try finding it in user_data
        # (This path is usually taken when navigating BACK to the menu from a sub-state)
        fatwa_id = context.user_data.get('edit_fatwa_id')

        if not fatwa_id:
            # Fallback failed
            if query:
                logger.error(f"Could not parse fatwa_id from query.data: {query.data} and not in user_data")
                await query.edit_message_text("❌ خطأ في تحديد الفتوى.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]]))
            else:
                await update.message.reply_text("❌ خطأ في تحديد الفتوى.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]]))
            return ConversationHandler.END

    context.user_data['edit_fatwa_id'] = fatwa_id

    keyboard = [
        [InlineKeyboardButton("العنوان", callback_data="edit_field_title"), InlineKeyboardButton("العالم", callback_data="edit_field_scholar")],
        [InlineKeyboardButton("السؤال", callback_data="edit_field_question"), InlineKeyboardButton("النص", callback_data="edit_field_text")],
        [InlineKeyboardButton("🏷️ التصنيف الفقهي", callback_data="edit_slot_1"), InlineKeyboardButton("🏷️ التصنيف الموضوعي", callback_data="edit_slot_2")],
        [InlineKeyboardButton("المصدر", callback_data="edit_field_source_name"), InlineKeyboardButton("عنوان المصدر", callback_data="edit_field_source_title")],
        [InlineKeyboardButton("رابط المصدر", callback_data="edit_field_source_url"), InlineKeyboardButton("الرابط الصوتي", callback_data="edit_field_audio")],
        [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_edit")]
    ]

    text = "✏️ **تعديل الفتوى**\n\nاختر الحقل المراد تعديله:"
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

    return STATE_EDIT_MENU

async def handle_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "cancel_edit":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        await view_fatwa(update, context, fatwa_id=fatwa_id)
        return ConversationHandler.END

    if data.startswith("edit_fatwa_"):
        fatwa_id = int(data.split('_')[-1])
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    if data.startswith("edit_sch_page_"):
        page = int(data.split('_')[-1])
        return await _show_edit_scholar_picker(update, context, page=max(0, page))

    if data.startswith("edit_pick_scholar_"):
        scholar_id = int(data.split('_')[-1])
        scholar = db.get_scholar_by_id(scholar_id)
        fatwa_id = context.user_data.get('edit_fatwa_id')
        if not scholar or not fatwa_id:
            await query.answer("❌ تعذر اختيار العالم.", show_alert=True)
            return STATE_EDIT_MENU
        db.update_fatwa(fatwa_id, {'scholar_name': scholar.get('name', '')})
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    if data == "edit_add_new_scholar":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        fatwa = db.get_fatwa(fatwa_id) if fatwa_id else {}
        back_cb = f"edit_fatwa_{fatwa_id}" if fatwa_id else "cancel_edit"
        context.user_data['edit_field'] = "scholar_name"
        text, markup = _build_edit_field_prompt(fatwa or {}, "scholar_name", back_cb)
        await query.edit_message_text(text, reply_markup=markup)
        return STATE_EDIT_VALUE

    clear_field_map = {
        "edit_clear_source_name": "source_name",
        "edit_clear_source_title": "source_title",
        "edit_clear_source_url": "source_url",
        "edit_clear_audio_url": "audio_url",
    }
    if data in clear_field_map:
        fatwa_id = context.user_data.get('edit_fatwa_id')
        if not fatwa_id:
            await query.answer("❌ تعذر تحديد الفتوى.", show_alert=True)
            return STATE_EDIT_MENU

        field = clear_field_map[data]
        if field == "source_name":
            db.update_fatwa(fatwa_id, {'source_name': ''})
        else:
            db.update_fatwa(fatwa_id, {field: ''})

        updated_fatwa = db.get_fatwa(fatwa_id) or {}
        back_cb = f"edit_fatwa_{fatwa_id}"
        context.user_data['edit_field'] = field
        text, markup = _build_edit_field_prompt(updated_fatwa, field, back_cb)
        await query.edit_message_text(text, reply_markup=markup)
        return STATE_EDIT_VALUE

    if data.startswith("edit_slot_"):
        slot = int(data.split('_')[-1])
        context.user_data['edit_taxonomy_slot'] = slot

        fatwa_id = context.user_data.get('edit_fatwa_id')
        fatwa = db.get_fatwa(fatwa_id) if fatwa_id else {}
        label = "الفقهي" if slot == 1 else "الموضوعي"
        summary = _format_edit_slot_summary(fatwa or {}, slot)

        actions = [
            InlineKeyboardButton("تغيير التصنيف الحالي", callback_data=f"edit_tax_cat_{slot}"),
            InlineKeyboardButton("تعديل المواضيع", callback_data=f"edit_tax_top_{slot}"),
            InlineKeyboardButton("➕ إضافة تصنيف آخر", callback_data=f"add_another_cat_{slot}"),
            InlineKeyboardButton("🗑️ حذف كافة التصنيفات", callback_data="delete_all_fatwa_classifications"),
        ]
        if slot == 2:
            actions.append(InlineKeyboardButton("🗑️ حذف هذا النوع بالكامل", callback_data="delete_slot_2"))

        keyboard = _pair_buttons(actions)
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"edit_fatwa_{fatwa_id}")])

        await query.edit_message_text(
            f"🏷️ تعديل النوع {label}\n\n"
            f"التصنيفات الحالية:\n{summary}\n\n"
            "اختر الإجراء:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return STATE_EDIT_MENU

    if data.startswith("add_another_cat_"):
        slot = int(data.split('_')[-1])
        context.user_data['edit_taxonomy_slot'] = slot
        context.user_data['edit_tax_action'] = 'add'
        context.user_data['edit_cat_search_query'] = None
        await show_edit_categories_step(update, context)
        return STATE_EDIT_CATEGORY

    if data.startswith("edit_tax_cat_"):
        slot = int(data.split('_')[-1])
        context.user_data['edit_taxonomy_slot'] = slot
        context.user_data['edit_tax_action'] = 'update'
        context.user_data['edit_field'] = "category_ids"
        await show_edit_categories_step(update, context)
        return STATE_EDIT_CATEGORY

    if data.startswith("edit_tax_top_"):
        slot = int(data.split('_')[-1])
        context.user_data['edit_taxonomy_slot'] = slot
        context.user_data['edit_field'] = "topic_ids"
        fatwa_id = context.user_data.get('edit_fatwa_id')
        context.user_data['edit_top_search_query'] = None

        # Get categories for this slot
        fatwa = db.get_fatwa(fatwa_id)
        classifications = fatwa.get('classifications', [])
        slot_cats = [cls for cls in classifications if cls['slot_index'] == slot]

        if not slot_cats:
            await query.answer("❌ يجب اختيار تصنيف أولاً لهذا النوع!", show_alert=True)
            return STATE_EDIT_MENU

        if len(slot_cats) > 1:
            # عرض قائمة لاختيار أي تصنيف نريد تعديل مواضيعه
            keyboard = []
            for cls in slot_cats:
                keyboard.append([InlineKeyboardButton(f"\U0001f4c2 {cls['category_name']}", callback_data=f"edit_topics_for_cat_{cls['category_id']}")])
            keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"edit_slot_{slot}")])

            await query.edit_message_text("📑 **اختر التصنيف الذي تريد تعديل مواضيعه:**", reply_markup=InlineKeyboardMarkup(keyboard))
            return STATE_EDIT_MENU
        else:
            cat_id = slot_cats[0]['category_id']
            context.user_data['edit_topic_cat_id'] = cat_id
            await show_edit_topics_step(update, context, cat_id)
            return STATE_EDIT_TOPIC

    if data == "delete_all_fatwa_classifications":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        if fatwa_id:
            db.update_fatwa(fatwa_id, {'classifications': []})
            await query.answer("✅ تم حذف كافة التصنيفات.", show_alert=True)
            return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
        return STATE_EDIT_MENU

    if data.startswith("edit_topics_for_cat_"):
        cat_id = int(data.split('_')[-1])
        context.user_data['edit_topic_cat_id'] = cat_id
        context.user_data['edit_top_search_query'] = None
        await show_edit_topics_step(update, context, cat_id)
        return STATE_EDIT_TOPIC

    if data == "delete_slot_2":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        conn = db.get_connection()
        c = conn.cursor()
        # Remove topic-type categories and their topics for this fatwa
        c.execute("""
            DELETE FROM fatwa_topics
            WHERE fatwa_id = ?
            AND topic_id IN (
                SELECT t.id FROM topics t
                JOIN categories c ON t.category_id = c.id
                WHERE c.type = 'topic'
            )
        """, (fatwa_id,))
        c.execute("""
            DELETE FROM fatwa_categories
            WHERE fatwa_id = ?
            AND category_id IN (SELECT id FROM categories WHERE type = 'topic')
        """, (fatwa_id,))
        conn.commit()
        conn.close()
        await query.answer("✅ تم حذف التصنيف الثاني.")
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    field_map = {
        "edit_field_title": "title",
        "edit_field_scholar": "scholar_name",
        "edit_field_question": "question",
        "edit_field_text": "answer",
        "edit_field_source_name": "source_name",
        "edit_field_source_title": "source_title",
        "edit_field_source_url": "source_url",
        "edit_field_audio": "audio_url"
    }

    field = field_map.get(data)
    if field:
        fatwa_id = context.user_data.get('edit_fatwa_id')
        fatwa = db.get_fatwa(fatwa_id) if fatwa_id else {}
        back_cb = f"edit_fatwa_{fatwa_id}" if fatwa_id else "cancel_edit"

        if field == "scholar_name":
            return await _show_edit_scholar_picker(update, context, page=0)

        context.user_data['edit_field'] = field
        text, markup = _build_edit_field_prompt(fatwa or {}, field, back_cb)
        await query.edit_message_text(text, reply_markup=markup)
        return STATE_EDIT_VALUE

    return STATE_EDIT_MENU

async def receive_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text
    field = context.user_data.get('edit_field')
    fatwa_id = context.user_data.get('edit_fatwa_id')

    if field and fatwa_id:
        db.update_fatwa(fatwa_id, {field: new_value})
        await update.message.reply_text("✅ تم التحديث بنجاح.")

        # العودة لقائمة التعديل أو العرض؟
        # العرض أفضل للتأكيد
        # Cannot easily edit existing message from here without ID, so just send new view?
        # Or finish conversation.

        # Let's show the updated fatwa (preview for long ones)
        fatwa = db.get_fatwa(fatwa_id)
        preview_text, is_long = build_fatwa_preview_text(fatwa, max_length=3600)
        text = preview_text if is_long else format_fatwa_content(fatwa, use_markdown=False)
        # Reconstruct buttons (simplified, mostly admin)
        view_cb = _resolve_view_context_data(context, fatwa_id) or f"view_{fatwa_id}"
        keyboard = [
            [InlineKeyboardButton("✏️ متابعة التعديل", callback_data=f"edit_fatwa_{fatwa_id}")],
            [InlineKeyboardButton("📖 عرض الفتوى", callback_data=view_cb)]
        ]
        if is_long:
            bot_username = context.bot.username
            fatwa_num = fatwa.get('fatwa_number', fatwa_id)
            deep_link = f"https://t.me/{bot_username}?start=fatwa_{fatwa_num}"
            keyboard.insert(1, [InlineKeyboardButton("تابع القراءة", url=deep_link)])
        from core.utils import split_long_message
        parts = split_long_message(text)
        for i, part in enumerate(parts):
            is_last = (i == len(parts) - 1)
            await update.message.reply_text(
                part,
                reply_markup=InlineKeyboardMarkup(keyboard) if is_last else None,
                disable_web_page_preview=True
            )

    return ConversationHandler.END

# --- دالات تدعم تعديل التصنيف والموضوع ---

async def show_edit_categories_step(update, context, page=0, search_query=None):
    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    if search_query is None:
        search_query = context.user_data.get('edit_cat_search_query')

    slot = context.user_data.get('edit_taxonomy_slot', 1)
    cat_type = "fiqh" if slot == 1 else "topic"

    cats = db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type)
    total_count = db.get_categories_count(search_query=search_query, category_type=cat_type)

    keyboard = []
    row = []
    for cid, name in cats:
        row.append(InlineKeyboardButton(name, callback_data=f"edit_cat_{cid}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"edit_cat_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"edit_cat_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    # أزرار البحث والإضافة
    action_row = [
        InlineKeyboardButton("🔍 بحث تصنيف", callback_data="search_edit_cat"),
        InlineKeyboardButton("➕ تصنيف جديد", callback_data="add_new_edit_cat")
    ]
    keyboard.append(action_row)

    # زر حذف الكل
    keyboard.append([InlineKeyboardButton("🗑️ حذف كافة التصنيفات", callback_data="delete_all_fatwa_classifications")])

    fatwa_id = context.user_data.get('edit_fatwa_id')
    back_cb = f"edit_fatwa_{fatwa_id}" if fatwa_id else "cancel_edit"
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=back_cb)])

    msg_text = "🏷️ **اختر التصنيف الجديد للفتوى:**"
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(msg_text, reply_markup=reply_markup)

async def handle_edit_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("edit_fatwa_"):
        fatwa_id = int(data.split('_')[-1])
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    if data == "search_edit_cat":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        back_cb = f"edit_fatwa_{fatwa_id}" if fatwa_id else "cancel_edit"
        await query.edit_message_text(
            "🔍 أرسل اسم التصنيف للبحث عنه:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء البحث", callback_data="edit_cat_search_cancel")],
                [InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=back_cb)]
            ])
        )
        return STATE_EDIT_CAT_SEARCH
    elif data == "edit_cat_search_cancel":
        context.user_data['edit_cat_search_query'] = None
        await show_edit_categories_step(update, context, page=0, search_query=None)
        return STATE_EDIT_CATEGORY
    elif data == "add_new_edit_cat":
        await query.edit_message_text(
            "🏷️ أرسل اسم التصنيف الجديد:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_new_cat")]
            ])
        )
        return STATE_EDIT_NEW_CAT
    elif data == "cancel_new_cat":
        await show_edit_categories_step(update, context)
        return STATE_EDIT_CATEGORY

    elif data == "delete_all_fatwa_classifications":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        if fatwa_id:
            db.update_fatwa(fatwa_id, {'classifications': []})
            await query.answer("✅ تم حذف كافة التصنيفات.", show_alert=True)
            return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
        return STATE_EDIT_CATEGORY

    elif data == "cancel_edit":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
        return STATE_EDIT_MENU
    elif data.startswith("edit_cat_page_"):
        page = int(data.split('_')[-1])
        await show_edit_categories_step(update, context, page)
        return STATE_EDIT_CATEGORY
    elif data.startswith("edit_cat_"):
        cat_id = int(data.split('_')[-1])
        fatwa_id = context.user_data.get('edit_fatwa_id')
        slot = context.user_data.get('edit_taxonomy_slot', 1)
        prev_cat_id = context.user_data.get('edit_topic_cat_id')
        if prev_cat_id and prev_cat_id != cat_id:
            context.user_data['edit_top_search_query'] = None

        # جلب التصنيفات الحالية وتحديث السلوت المطلوب
        fatwa = db.get_fatwa(fatwa_id)
        current_classifications = fatwa.get('classifications', [])
        action = context.user_data.get('edit_tax_action', 'update')

        # تحديث أو إضافة
        updated = False
        if action == 'update':
            for cls in current_classifications:
                if cls['slot_index'] == slot:
                    cls['category_id'] = cat_id
                    cls['topic_ids'] = [] # تصفير المواضيع عند تغيير التصنيف
                    updated = True
                    break

        if not updated: # Case is 'add' or 'update' but slot not found
            current_classifications.append({'category_id': cat_id, 'topic_ids': [], 'slot_index': slot})

        db.update_fatwa(fatwa_id, {'classifications': current_classifications})

        # التوجه تلقائياً لاختيار المواضيع لهذا التصنيف المختار
        context.user_data['edit_topic_cat_id'] = cat_id
        await query.answer(f"✅ تم تحديث التصنيف.")
        return await show_edit_topics_step(update, context, cat_id=cat_id)

async def show_edit_topics_step(update, context, cat_id=None, page=0, search_query=None):
    """عرض قائمة المواضيع للتعديل - دعم الاختيار المتعدد"""
    ITEMS_PER_PAGE = 8
    offset = page * ITEMS_PER_PAGE

    if search_query is None:
        search_query = context.user_data.get('edit_top_search_query')

    if cat_id:
        context.user_data['edit_topic_cat_id'] = cat_id
        topics = db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)
        total_count = db.get_topics_count(cat_id, search_query=search_query)
        cat_row = db.get_category(cat_id)
        cat_name = cat_row['name'] if cat_row else "غير محدد"
    else:
        # Fallback (should ideally never happen in edit)
        all_topics = db.get_topics()
        total_count = len(all_topics)
        topics = all_topics[offset:offset+ITEMS_PER_PAGE]
        cat_name = "كل المواضيع"

    slot = context.user_data.get('edit_taxonomy_slot', 1)
    # جلب المواضيع المحددة حالياً من قاعدة البيانات أو الذاكرة المؤقتة
    key = f'edit_selected_topics_slot_{slot}_cat_{cat_id}'
    if key not in context.user_data:
        fatwa = db.get_fatwa(context.user_data.get('edit_fatwa_id'))
        classifications = fatwa.get('classifications', [])
        current_topics = []
        for cls in classifications:
            if cls['slot_index'] == slot and cls['category_id'] == cat_id:
                current_topics = cls.get('topic_ids', [])
                break
        context.user_data[key] = current_topics

    selected_topics = context.user_data[key]

    keyboard = []
    row = []
    for tid, name in topics:
        is_selected = tid in selected_topics
        text = f"\u2705 {name}" if is_selected else name
        row.append(InlineKeyboardButton(text, callback_data=f"edit_toggle_top_{tid}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav_buttons = []
    cb_prefix = f"edit_top_page_{cat_id}_" if cat_id else "edit_top_page_none_"
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"{cb_prefix}{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"{cb_prefix}{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    # أزرار البحث والإضافة
    keyboard.append([
        InlineKeyboardButton("🔍 بحث موضوع", callback_data="search_edit_top"),
        InlineKeyboardButton("➕ موضوع جديد", callback_data="add_new_edit_top")
    ])

    # زر الإتمام وزر الرجوع
    fatwa_id = context.user_data.get('edit_fatwa_id')
    back_cb = f"edit_fatwa_{fatwa_id}" if fatwa_id else "cancel_edit"
    keyboard.append([
        InlineKeyboardButton("📌 حفظ المواضيع", callback_data="edit_done_topics"),
        InlineKeyboardButton("🗑️ حذف الكل", callback_data="delete_all_fatwa_classifications")
    ])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=back_cb)])

    msg_text = f"🏷️ التصنيف: **{cat_name}**\n📑 **عدل المواضيع المختارة:**"
    reply_markup = InlineKeyboardMarkup(keyboard)

    if hasattr(update, 'callback_query') and update.callback_query:
        await update.callback_query.edit_message_text(msg_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif hasattr(update, 'message') and update.message:
        await update.message.reply_text(msg_text, reply_markup=reply_markup, parse_mode='Markdown')

    return STATE_EDIT_TOPIC

async def handle_edit_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("edit_fatwa_"):
        fatwa_id = int(data.split('_')[-1])
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)

    if data == "search_edit_top":
        cat_id = context.user_data.get('edit_topic_cat_id')
        cb_data = f"edit_top_page_{cat_id}_0" if cat_id else "edit_top_page_none_0"
        fatwa_id = context.user_data.get('edit_fatwa_id')
        back_cb = f"edit_fatwa_{fatwa_id}" if fatwa_id else "cancel_edit"

        await query.edit_message_text(
            "🔍 أرسل اسم الموضوع للبحث عنه:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء البحث", callback_data="edit_top_search_cancel")],
                [InlineKeyboardButton("🔙 رجوع للتعديل", callback_data=back_cb)]
            ])
        )
        return STATE_EDIT_TOP_SEARCH
    elif data == "edit_top_search_cancel":
        context.user_data['edit_top_search_query'] = None
        cat_id = context.user_data.get('edit_topic_cat_id')
        await show_edit_topics_step(update, context, cat_id=cat_id, page=0, search_query=None)
        return STATE_EDIT_TOPIC
    elif data == "add_new_edit_top":
        await query.edit_message_text(
            "📑 أرسل اسم الموضوع الجديد:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 إلغاء", callback_data="cancel_new_top")]
            ])
        )
        return STATE_EDIT_NEW_TOP
    elif data == "cancel_new_top":
        cat_id = context.user_data.get('edit_topic_cat_id')
        await show_edit_topics_step(update, context, cat_id=cat_id)
        return STATE_EDIT_TOPIC
    elif data == "cancel_edit":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
        return STATE_EDIT_MENU

    elif data == "delete_all_fatwa_classifications":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        if fatwa_id:
            db.update_fatwa(fatwa_id, {'classifications': []})
            await query.answer("✅ تم حذف كافة التصنيفات.", show_alert=True)
            return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)
        return STATE_EDIT_TOPIC
    elif data.startswith("edit_top_page_"):
        parts = data.split('_')
        cat_id = int(parts[3]) if parts[3] != 'none' else None
        page = int(parts[4])
        # Cache cat_id for search context if needed
        context.user_data['edit_topic_cat_id'] = cat_id
        await show_edit_topics_step(update, context, cat_id, page)
        return STATE_EDIT_TOPIC

    elif data.startswith("edit_toggle_top_"):
        topic_id = int(data.split('_')[-1])
        slot = context.user_data.get('edit_taxonomy_slot', 1)
        cat_id = context.user_data.get('edit_topic_cat_id')
        key = f'edit_selected_topics_slot_{slot}_cat_{cat_id}'

        if key not in context.user_data:
            context.user_data[key] = []

        if topic_id in context.user_data[key]:
            context.user_data[key].remove(topic_id)
        else:
            context.user_data[key].append(topic_id)

        await show_edit_topics_step(update, context, context.user_data.get('edit_topic_cat_id'))
        return STATE_EDIT_TOPIC

    elif data == "edit_done_topics":
        fatwa_id = context.user_data.get('edit_fatwa_id')
        slot = context.user_data.get('edit_taxonomy_slot', 1)
        cat_id = context.user_data.get('edit_topic_cat_id')
        key = f'edit_selected_topics_slot_{slot}_cat_{cat_id}'
        topic_ids = context.user_data.get(key, [])

        fatwa = db.get_fatwa(fatwa_id)
        current_classifications = fatwa.get('classifications', [])

        for cls in current_classifications:
            if cls['slot_index'] == slot and cls['category_id'] == cat_id:
                cls['topic_ids'] = topic_ids
                break

        db.update_fatwa(fatwa_id, {'classifications': current_classifications})

        # تنظيف
        context.user_data.pop(key, None)
        context.user_data.pop('edit_topic_cat_id', None)

        await query.edit_message_text(f"✅ تم تحديث مواضيع التصنيف بنجاح.")
        return await start_edit_fatwa(update, context, fatwa_id=fatwa_id)


    # --- New Handlers for Edit Search/Add (Category) ---
    # ... existing ...

async def handle_edit_cat_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = update.message.text.strip()
    context.user_data['edit_cat_search_query'] = search_query
    await show_edit_categories_step(update, context, page=0, search_query=search_query)
    return STATE_EDIT_CATEGORY

async def handle_receive_new_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_name = update.message.text.strip()
    slot = context.user_data.get('edit_taxonomy_slot', 1)
    cat_type = "fiqh" if slot == 1 else "topic"
    cat_id = db.add_category(cat_name, category_type=cat_type)
    if not cat_id:
        cats = db.get_categories(category_type=cat_type)
        for cid, name in cats:
            if name == cat_name:
                cat_id = cid
                break

    if cat_id:
        fatwa_id = context.user_data.get('edit_fatwa_id')
        fatwa = db.get_fatwa(fatwa_id)
        current_classifications = fatwa.get('classifications', [])
        action = context.user_data.get('edit_tax_action', 'update')

        updated = False
        if action == 'update':
            for cls in current_classifications:
                if cls['slot_index'] == slot:
                    cls['category_id'] = cat_id
                    cls['topic_ids'] = []
                    updated = True
                    break

        if not updated:
            current_classifications.append({'category_id': cat_id, 'topic_ids': [], 'slot_index': slot})

        db.update_fatwa(fatwa_id, {'classifications': current_classifications})

        context.user_data['edit_topic_cat_id'] = cat_id
        await update.message.reply_text(f"✅ تم إضافة التصنيف: {cat_name}")
        return await show_edit_topics_step(update, context, cat_id=cat_id)
    else:
        await update.message.reply_text("❌ حدث خطأ أثناء إضافة التصنيف.")
        return STATE_EDIT_MENU

async def handle_edit_top_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = update.message.text.strip()
    context.user_data['edit_top_search_query'] = search_query
    cat_id = context.user_data.get('edit_topic_cat_id')
    await show_edit_topics_step(update, context, cat_id=cat_id, page=0, search_query=search_query)
    return STATE_EDIT_TOPIC

async def handle_receive_new_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    cat_id = context.user_data.get('edit_topic_cat_id')

    # Try to add the new topic
    msg = ""
    topic_id = db.add_topic(name, category_id=cat_id)
    if topic_id:
        msg = f"✅ تم إضافة واختيار الموضوع: {name}"
    else:
        # If add failed, check if it already exists
        topic_id = db.get_topic_id_by_name(name, category_id=cat_id)
        if topic_id:
            msg = f"⚠️ الموضوع '{name}' موجود مسبقاً، تم اختياره."
        else:
            await update.message.reply_text("❌ حدث خطأ أثناء إضافة الموضوع.")
            return STATE_EDIT_MENU

    if topic_id:
        fatwa_id = context.user_data.get('edit_fatwa_id')
        slot = context.user_data.get('edit_taxonomy_slot', 1)

        fatwa = db.get_fatwa(fatwa_id)
        current_classifications = fatwa.get('classifications', [])

        # In original logic, it probably just added the topic to the first classification
        # تحديث قائمة المواضيع في التصنيف المطلوب (مع التأكد من السلوت والتصنيف)
        for cls in current_classifications:
            if cls['slot_index'] == slot and cls['category_id'] == cat_id:
                if 'topic_ids' not in cls:
                    cls['topic_ids'] = []
                if topic_id not in cls['topic_ids']:
                    cls['topic_ids'].append(topic_id)
                break

        db.update_fatwa(fatwa_id, {'classifications': current_classifications})

        # تنظيف استعلام البحث لضمان رؤية الموضوع الجديد
        context.user_data['edit_top_search_query'] = None

        await update.message.reply_text(msg)
        return await show_edit_topics_step(update, context, cat_id=cat_id)


edit_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(start_edit_fatwa, pattern='^edit_fatwa_')],
    states={
        STATE_EDIT_MENU: [CallbackQueryHandler(handle_edit_menu)],
        STATE_EDIT_VALUE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_value),
            CallbackQueryHandler(handle_edit_menu, pattern='^edit_clear_'),
            CallbackQueryHandler(handle_edit_menu, pattern='^cancel_edit$'),
            CallbackQueryHandler(handle_edit_menu, pattern='^edit_fatwa_')
        ],
        STATE_EDIT_CATEGORY: [CallbackQueryHandler(handle_edit_category_selection)],
        STATE_EDIT_TOPIC: [CallbackQueryHandler(handle_edit_topic_selection)],

        STATE_EDIT_CAT_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_cat_search),
            CallbackQueryHandler(handle_edit_category_selection, pattern='^edit_cat_page_'),
            CallbackQueryHandler(handle_edit_category_selection, pattern='^edit_fatwa_'),
            CallbackQueryHandler(handle_edit_category_selection, pattern='^edit_cat_search_cancel$'),
            CallbackQueryHandler(handle_edit_category_selection, pattern='^cancel_edit$')
        ],
        STATE_EDIT_NEW_CAT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_new_cat),
            CallbackQueryHandler(handle_edit_category_selection, pattern='^cancel_new_cat$')
        ],

        STATE_EDIT_TOP_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_top_search),
            CallbackQueryHandler(handle_edit_topic_selection, pattern='^edit_top_page_'),
            CallbackQueryHandler(handle_edit_topic_selection, pattern='^edit_fatwa_'),
            CallbackQueryHandler(handle_edit_topic_selection, pattern='^edit_top_search_cancel$'),
            CallbackQueryHandler(handle_edit_topic_selection, pattern='^cancel_edit$')
        ],
        STATE_EDIT_NEW_TOP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_receive_new_top),
            CallbackQueryHandler(handle_edit_topic_selection, pattern='^cancel_new_top$')
        ]
    },
    fallbacks=[CallbackQueryHandler(cancel_operation, pattern='^cancel$'), CommandHandler('cancel', cancel_operation)]
)
