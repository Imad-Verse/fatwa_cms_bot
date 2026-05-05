import logging
import re
from types import SimpleNamespace
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.utils import (
    format_fatwa_card,
    format_fatwa_content,
    build_fatwa_preview_text,
    split_long_message,
    format_full_fatwa_for_copy,
    back_to_main_keyboard,
    back_to_search_keyboard,
    safe_reply_text,
    safe_edit_message_text,
)
from core.keyboards import create_fatwa_view_keyboard
from handlers.fatwa_utils import _build_view_back_button, _extract_context_suffix, _resolve_view_context_data

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

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
    query = update.callback_query
    if view_context_data is None:
        await query.answer()

    try:
        if fatwa_id is None:
            parts = query.data.split('_')
            fatwa_id = int(parts[1])

        current_view_data = view_context_data or query.data or f"view_{fatwa_id}"
        fatwa = db.get_fatwa(fatwa_id)

        if not fatwa:
            await query.edit_message_text("❌ الفتوى غير موجودة.", reply_markup=back_to_search_keyboard("🔙 رجوع"))
            return

        is_admin = bot_db.is_admin(update.effective_user.id)
        if not is_admin and fatwa.get('status') != 'published':
            back_btn = _build_view_back_button(SimpleNamespace(data=current_view_data), context, fatwa_id=fatwa_id)
            await query.edit_message_text(
                "❌ هذه الفتوى غير منشورة.",
                reply_markup=InlineKeyboardMarkup([[back_btn]])
            )
            return

        if not force_full:
            db.increment_views(fatwa_id)

        preview_text, is_long = build_fatwa_preview_text(fatwa, max_length=3600)
        text = format_fatwa_content(fatwa, use_markdown=False) if force_full else (preview_text if is_long else format_fatwa_content(fatwa, use_markdown=False))

        is_favorite = bot_db.is_favorite(update.effective_user.id, fatwa_id)
        context.user_data['last_view_data'] = current_view_data
        context.user_data['last_view_fatwa_id'] = fatwa_id
        back_btn = _build_view_back_button(SimpleNamespace(data=current_view_data), context, fatwa_id=fatwa_id)

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

        message_parts = split_long_message(text)
        await query.edit_message_text(
            message_parts[0],
            reply_markup=reply_markup if len(message_parts) == 1 else None,
            disable_web_page_preview=True
        )

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
        except Exception:
            try:
                await query.message.reply_text("❌ حدث خطأ أثناء عرض الفتوى.", reply_markup=back_to_main_keyboard())
            except Exception:
                pass

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
    from core.utils import create_main_keyboard
    try:
        fatwa = db.get_fatwa(fatwa_id)
        if not fatwa:
            await update.message.reply_text("❌ الفتوى غير موجودة.", reply_markup=create_main_keyboard())
            return

        is_admin = bot_db.is_admin(update.effective_user.id)
        if not is_admin and fatwa.get('status') != 'published':
            await update.message.reply_text("❌ هذه الفتوى غير منشورة.", reply_markup=create_main_keyboard(is_admin))
            return

        db.increment_views(fatwa_id)
        text = format_fatwa_content(fatwa, use_markdown=False)

        keyboard = []
        link_buttons = []
        if fatwa.get('source_url'):
            link_buttons.append(InlineKeyboardButton("📚 الانتقال للمصدر", url=fatwa['source_url']))
        if fatwa.get('audio_url'):
            link_buttons.append(InlineKeyboardButton("🎧 سماع الصوتية", url=fatwa['audio_url']))
        if link_buttons:
            keyboard.append(link_buttons)

        action_buttons = []
        is_fav = bot_db.is_favorite(update.effective_user.id, fatwa_id)
        fav_text = "❌ حذف من المفضلة" if is_fav else "⭐ مفضلة"
        action_buttons.append(InlineKeyboardButton(fav_text, callback_data=f"toggle_fav_{fatwa_id}"))

        from urllib.parse import quote
        report_msg = f"السلام عليكم ورحمة الله وبركاته\nأريد الابلاغ عن فتوى التي تحمل رقم: {fatwa.get('fatwa_number', fatwa_id)}"
        report_url = f"https://t.me/abulharith_imad?text={quote(report_msg)}"
        action_buttons.append(InlineKeyboardButton("⚠️ إبلاغ", url=report_url))
        keyboard.append(action_buttons)

        if is_admin:
            keyboard.append([
                InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}"),
                InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}")
            ])
            keyboard.append([InlineKeyboardButton("📢 إرسال الفتوى", callback_data=f"broadcast_{fatwa_id}")])

        keyboard.append([
            InlineKeyboardButton("📋 نسخ النص", callback_data=f"copy_full_{fatwa_id}"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)
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

async def copy_fatwa_full(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نسخ الفتوى كاملة مع الروابط داخل النص"""
    query = update.callback_query
    fatwa_id = int(query.data.split('_')[2])
    fatwa = db.get_fatwa(fatwa_id)

    if fatwa:
        text = format_full_fatwa_for_copy(fatwa)
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
            await query.answer("تعذر تحديد حسابك. اضغط الزر من حسابك الشخصي ثم أعد المحاولة.", show_alert=True)
            return

        try:
            for part in message_parts:
                await context.bot.send_message(chat_id=recipient_user.id, text=part)
            await query.answer("📬 تم إرسال نسخة الفتوى إلى الخاص")
        except Exception:
            bot_username = context.bot.username or "Fatwa_CMS_Bot"
            await query.answer(f"تعذر الإرسال إلى الخاص. افتح @{bot_username} أولاً ثم أعد المحاولة.", show_alert=True)
    else:
        await query.answer("❌ خطأ: الفتوى غير موجودة")
