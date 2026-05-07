"""
معالج إرسال الفتاوى للقنوات من طرف المستخدم (handlers/user_publish.py)
----------------------------------------------------------------------
يتيح لأي مستخدم إرسال فتوى من إعدادات النشر المحدد إلى قنواته/مجموعاته
التي يملكها أو يشرف عليها والبوت مشرف فيها.

الأزرار (callback_data):
    user_send_fatwa          → لوحة عرض القنوات (الصفحة الأولى)
    user_sf_page_{n}         → التنقل بين الصفحات
    user_sf_toggle_{id}_{p}  → تحديد/إلغاء قناة أو مجموعة
    user_sf_selall           → تحديد الكل
    user_sf_send             → تأكيد الإرسال
    user_sf_send_valid       → إرسال للقنوات الصالحة فقط
    user_sf_cancel           → إلغاء وعودة
"""

import logging
import asyncio
import html
import time
from typing import Dict, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import ContextTypes
from telegram.error import BadRequest, Forbidden

from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.keyboards import create_published_fatwa_keyboard
from core.utils import build_fatwa_preview_text, escape_markdown

logger = logging.getLogger(__name__)

db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

ITEMS_PER_PAGE = 8  # 4 صفوف × 2 عمود
USER_SF_SELECTED_KEY = "user_sf_selected"        # set of chat_ids
USER_SF_CHANNELS_KEY = "user_sf_user_channels"    # cached list of user's channels
USER_SF_CHANNELS_TS_KEY = "user_sf_channels_ts"   # timestamp of cache
USER_SF_CHANNELS_TTL = 120  # seconds before re-fetching user channels


# ==================== Helpers ====================


def _get_publish_settings() -> Tuple[Optional[int], List[int]]:
    """قراءة إعدادات النشر المحدد من الإعدادات العامة للبوت."""
    raw_cat = (bot_db.get_setting("auto_publish_category_id", "") or "").strip()
    if not raw_cat:
        return None, []
    try:
        cat_id = int(raw_cat)
    except (TypeError, ValueError):
        return None, []
    category = db.get_category(cat_id)
    if not category:
        return None, []

    raw_topics = (bot_db.get_setting("auto_publish_topic_ids", "") or "").strip()
    topic_ids: List[int] = []
    if raw_topics:
        for part in raw_topics.split(","):
            token = part.strip()
            if not token:
                continue
            try:
                val = int(token)
            except ValueError:
                continue
            if val > 0 and val not in topic_ids:
                topic_ids.append(val)
    return cat_id, topic_ids


async def _safe_edit(query, text: str, **kwargs):
    """تعديل الرسالة مع تجاهل خطأ عدم التعديل."""
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


async def _check_bot_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
    """التحقق من أن البوت مشرف في القناة/المجموعة."""
    try:
        member = await context.bot.get_chat_member(chat_id, context.bot.id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


async def _check_user_is_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """التحقق من أن المستخدم مشرف أو مالك في القناة/المجموعة."""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except Exception:
        return False


async def _get_user_channels(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    force_refresh: bool = False,
) -> List[Dict]:
    """
    جلب القنوات والمجموعات التي يملكها أو يشرف عليها المستخدم.
    يتم التخزين المؤقت لمدة USER_SF_CHANNELS_TTL ثانية لتجنب استدعاءات API المتكررة.
    """
    # فحص التخزين المؤقت
    cached = context.user_data.get(USER_SF_CHANNELS_KEY)
    cached_ts = context.user_data.get(USER_SF_CHANNELS_TS_KEY, 0)

    if not force_refresh and cached is not None and (time.time() - cached_ts) < USER_SF_CHANNELS_TTL:
        return cached

    # جلب جميع القنوات والمجموعات النشطة من قاعدة بيانات البوت
    all_channels = bot_db.get_channels(status="active")

    user_channels: List[Dict] = []

    for ch in all_channels:
        chat_id = ch["chat_id"]
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                user_channels.append(ch)
        except Exception as e:
            logger.debug(f"Could not check user {user_id} in chat {chat_id}: {e}")
            continue

    # ترتيب (قنوات أولاً ثم مجموعات)
    user_channels.sort(key=lambda ch: (0 if ch.get("type") == "channel" else 1, ch.get("title", "")))

    # تخزين مؤقت
    context.user_data[USER_SF_CHANNELS_KEY] = user_channels
    context.user_data[USER_SF_CHANNELS_TS_KEY] = time.time()

    return user_channels


# ==================== Panel ====================


async def user_send_fatwa_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة قنوات ومجموعات المستخدم مع اختيار متعدد."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    # تحقق أولاً: هل هناك إعدادات نشر محدد؟
    cat_id, topic_ids = _get_publish_settings()
    specific_enabled = bot_db.get_setting("auto_publish_specific", "0") == "1"

    if not cat_id and not specific_enabled:
        await _safe_edit(
            query,
            "⚠️ **لم يتم تفعيل إعدادات النشر المحدد بعد.**\n\n"
            "يرجى التواصل مع إدارة البوت لتفعيل خاصية النشر المحدد أولاً.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
            ]),
        )
        return

    # هل هذا أول فتح (ليس تنقل صفحات أو toggle)؟
    data = query.data or ""
    is_initial = data == "user_send_fatwa"

    # جلب قنوات ومجموعات المستخدم (مع تخزين مؤقت)
    # عند الفتح الأولي نعرض رسالة انتظار
    if is_initial:
        # مسح اختيارات سابقة
        context.user_data.pop(USER_SF_SELECTED_KEY, None)
        # إظهار رسالة تحميل
        await _safe_edit(
            query,
            "⏳ **جاري جلب قنواتك ومجموعاتك...**\n\nيرجى الانتظار قليلاً.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ إلغاء", callback_data="user_sf_cancel")]
            ]),
        )
        user_channels = await _get_user_channels(context, user_id, force_refresh=True)
    else:
        user_channels = await _get_user_channels(context, user_id, force_refresh=False)

    if not user_channels:
        await _safe_edit(
            query,
            "⚠️ **لا توجد قنوات أو مجموعات تملكها أو تشرف عليها حالياً.**\n\n"
            "تأكد من أنك مشرف أو مالك في القناة/المجموعة، وأن البوت مضاف كمشرف فيها أيضاً.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ كيفية إضافة البوت", callback_data="how_to_add_bot")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
            ]),
        )
        return

    # الصفحة الحالية: من callback_data أو من user_data كـ fallback
    page = 0
    if "user_sf_page_" in data:
        try:
            page = int(data.split("user_sf_page_")[-1])
        except (TypeError, ValueError):
            page = 0
    elif "user_sf_current_page" in context.user_data:
        page = context.user_data["user_sf_current_page"]

    page = max(page, 0)
    total_count = len(user_channels)
    total_pages = max((total_count - 1) // ITEMS_PER_PAGE + 1, 1)
    page = min(page, total_pages - 1)

    # حفظ الصفحة الحالية
    context.user_data["user_sf_current_page"] = page

    offset = page * ITEMS_PER_PAGE
    page_items = user_channels[offset: offset + ITEMS_PER_PAGE]

    # الحصول على الاختيارات الحالية
    selected: set = context.user_data.get(USER_SF_SELECTED_KEY, set())

    # بناء لوحة المفاتيح (عمودين - كل صف فيه قناتين)
    keyboard = []
    row = []
    for ch in page_items:
        chat_id = ch["chat_id"]
        title = ch.get("title") or "بدون عنوان"
        ch_type = ch.get("type", "channel")
        icon = "📢" if ch_type == "channel" else "👥"
        check = "✅" if chat_id in selected else "⬜"
        label = f"{check}{icon} {title}"
        # اقتطاع النص (حد تليجرام 64 حرف للزر)
        if len(label) > 30:
            label = label[:27] + "..."
        row.append(InlineKeyboardButton(label, callback_data=f"user_sf_toggle_{chat_id}_{page}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # أزرار التنقل
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"user_sf_page_{page - 1}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"user_sf_page_{page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    # عدد المختار
    selected_count = len(selected)
    send_label = f"📨 إرسال فتوى ({selected_count})" if selected_count > 0 else "📨 إرسال فتوى"

    # زر تحديد الكل / إلغاء الكل
    all_chat_ids = {ch["chat_id"] for ch in user_channels}
    if selected >= all_chat_ids and all_chat_ids:
        # الكل محدد → زر إلغاء الكل
        select_all_label = "☑️ إلغاء تحديد الكل"
    else:
        select_all_label = "✅ تحديد الكل"
    keyboard.append([InlineKeyboardButton(select_all_label, callback_data="user_sf_selall")])

    # أزرار الإرسال والإلغاء
    keyboard.append([
        InlineKeyboardButton(send_label, callback_data="user_sf_send"),
        InlineKeyboardButton("❌ إلغاء وعودة", callback_data="user_sf_cancel"),
    ])

    # نص العرض
    cat_name = ""
    if cat_id:
        cat = db.get_category(cat_id)
        if cat:
            cat_name = cat["name"]

    text = (
        "📨 **ارسل فتوى لقناتك**\n\n"
        "اختر القنوات والمجموعات التي تريد إرسال الفتوى إليها:\n"
        f"📂 تصنيف النشر: **{escape_markdown(cat_name or 'غير محدد')}**\n"
        f"📊 قنواتك: {total_count} | المحدد: {selected_count}\n"
        f"الصفحة: {page + 1}/{total_pages}"
    )

    await _safe_edit(
        query,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


# ==================== Toggle Selection ====================


async def toggle_user_channel_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل اختيار قناة/مجموعة."""
    query = update.callback_query

    data = query.data or ""
    # data format: user_sf_toggle_{chat_id}_{page}
    # chat_id can be negative (e.g. -1001234567890), so we use rfind
    suffix = data.replace("user_sf_toggle_", "")
    last_underscore = suffix.rfind("_")
    if last_underscore > 0:
        chat_id_str = suffix[:last_underscore]
        page_str = suffix[last_underscore + 1:]
    else:
        chat_id_str = suffix
        page_str = "0"

    try:
        chat_id = int(chat_id_str)
    except (TypeError, ValueError):
        await query.answer("⚠️ بيانات غير صالحة", show_alert=True)
        return

    try:
        page = int(page_str)
    except (TypeError, ValueError):
        page = 0

    selected: set = context.user_data.setdefault(USER_SF_SELECTED_KEY, set())

    if chat_id in selected:
        selected.discard(chat_id)
        await query.answer("❌ تم إلغاء التحديد")
    else:
        selected.add(chat_id)
        await query.answer("✅ تم التحديد")

    # إعادة عرض اللوحة في نفس الصفحة
    context.user_data["user_sf_current_page"] = page
    await user_send_fatwa_panel(update, context)


# ==================== Select All ====================


async def toggle_select_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحديد الكل أو إلغاء تحديد الكل."""
    query = update.callback_query
    user_id = update.effective_user.id

    user_channels = await _get_user_channels(context, user_id, force_refresh=False)
    all_chat_ids = {ch["chat_id"] for ch in user_channels}
    selected: set = context.user_data.setdefault(USER_SF_SELECTED_KEY, set())

    if selected >= all_chat_ids and all_chat_ids:
        # الكل محدد → إلغاء الكل
        selected.clear()
        await query.answer("❌ تم إلغاء تحديد الكل")
    else:
        # تحديد الكل
        selected.update(all_chat_ids)
        await query.answer("✅ تم تحديد الكل")

    # إعادة عرض اللوحة في نفس الصفحة (الصفحة محفوظة في user_data)
    await user_send_fatwa_panel(update, context)


# ==================== Execute Send ====================


async def user_send_fatwa_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من الصلاحيات وإرسال الفتوى للقنوات المختارة."""
    query = update.callback_query

    selected: set = context.user_data.get(USER_SF_SELECTED_KEY, set())

    if not selected:
        await query.answer("⚠️ لم تحدد أي قناة أو مجموعة!", show_alert=True)
        return

    await query.answer("⏳ جاري التحقق والإرسال...")

    # جلب إعدادات النشر المحدد
    cat_id, topic_ids = _get_publish_settings()

    if not cat_id:
        await _safe_edit(
            query,
            "⚠️ **لم يتم تحديد تصنيف للنشر.**\n"
            "يرجى التواصل مع إدارة البوت لتفعيل إعدادات النشر المحدد.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
            ]),
        )
        return

    # التحقق من صلاحيات البوت في كل قناة مختارة
    valid_channels: List[Dict] = []
    no_permission_channels: List[str] = []

    all_channels = bot_db.get_channels(status="active")
    channel_map = {ch["chat_id"]: ch for ch in all_channels}

    for chat_id in list(selected):
        ch = channel_map.get(chat_id)
        if not ch:
            selected.discard(chat_id)
            continue

        is_admin = await _check_bot_admin(context, chat_id)
        if is_admin:
            valid_channels.append(ch)
        else:
            title = ch.get("title") or "بدون عنوان"
            no_permission_channels.append(title)

    # إذا وجدت قنوات ليس البوت مشرفاً فيها
    if no_permission_channels:
        channels_list = "\n".join([f"• {html.escape(name)}" for name in no_permission_channels])
        text = (
            "⚠️ <b>البوت ليس مشرفاً في بعض القنوات/المجموعات المختارة:</b>\n\n"
            f"{channels_list}\n\n"
            "يرجى إضافة البوت كمشرف في هذه القنوات أولاً، ثم أعد المحاولة.\n\n"
        )

        if valid_channels:
            text += f"✅ سيتم الإرسال إلى {len(valid_channels)} قناة/مجموعة فقط."
            keyboard = [
                [InlineKeyboardButton(f"📨 إرسال للصالحة ({len(valid_channels)})", callback_data="user_sf_send_valid")],
                [InlineKeyboardButton("❌ إلغاء وعودة", callback_data="user_sf_cancel")],
            ]
        else:
            text += "لا توجد قنوات صالحة للإرسال."
            keyboard = [
                [InlineKeyboardButton("➕ كيفية إضافة البوت مشرفاً", callback_data="how_to_add_bot")],
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
            ]

        await _safe_edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        context.user_data["user_sf_valid_channels"] = valid_channels
        return

    # كل القنوات صالحة، ننفذ الإرسال
    await _do_send_fatwa(query, context, valid_channels, cat_id, topic_ids)


async def user_send_fatwa_send_valid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال الفتوى فقط للقنوات الصالحة (بعد تحذير الصلاحيات)."""
    query = update.callback_query
    await query.answer("⏳ جاري الإرسال...")

    valid_channels = context.user_data.get("user_sf_valid_channels", [])
    if not valid_channels:
        await query.answer("⚠️ لا توجد قنوات صالحة!", show_alert=True)
        return

    cat_id, topic_ids = _get_publish_settings()
    if not cat_id:
        await query.answer("⚠️ لم يتم تحديد تصنيف للنشر!", show_alert=True)
        return

    await _do_send_fatwa(query, context, valid_channels, cat_id, topic_ids)


async def _do_send_fatwa(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    channels: List[Dict],
    cat_id: int,
    topic_ids: List[int],
):
    """المنطق الفعلي لجلب الفتوى وإرسالها."""

    # جلب فتوى عشوائية من التصنيف/المواضيع المحددة
    fatwa = db.get_random_published_fatwa(
        category_id=cat_id,
        topic_ids=topic_ids if topic_ids else None,
    )

    if not fatwa:
        await _safe_edit(
            query,
            "⚠️ **لا توجد فتاوى متاحة** في التصنيف/المواضيع المحددة حالياً.\n"
            "يرجى التواصل مع إدارة البوت.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
            ]),
        )
        return

    # تجهيز نص الفتوى
    text_to_send, is_long = build_fatwa_preview_text(fatwa, max_length=3600)
    reply_markup = create_published_fatwa_keyboard(
        fatwa=fatwa,
        bot_username=context.bot.username,
        is_long=is_long,
    )

    # إرسال الفتوى
    success_count = 0
    fail_count = 0
    failed_names: List[str] = []

    for ch in channels:
        try:
            await context.bot.send_message(
                chat_id=ch["chat_id"],
                text=text_to_send,
                reply_markup=reply_markup,
            )
            success_count += 1
            await asyncio.sleep(0.1)
        except Exception as e:
            fail_count += 1
            failed_names.append(ch.get("title") or "غير معروف")
            logger.error(f"User publish failed for chat {ch.get('chat_id')}: {e}")

    # تحديث المشاهدات
    if success_count > 0:
        db.increment_views_by(fatwa["id"], success_count)

    # تقرير الإرسال
    fatwa_num = fatwa.get("fatwa_number", fatwa["id"])
    report_lines = [
        "✅ **تقرير إرسال الفتوى**\n",
        f"📖 الفتوى رقم: `{fatwa_num}`",
        f"📝 العنوان: {escape_markdown(fatwa.get('title', ''))}",
        "",
        f"📨 تم الإرسال بنجاح: {success_count}",
    ]

    if fail_count > 0:
        report_lines.append(f"❌ فشل الإرسال: {fail_count}")
        if failed_names:
            failed_list = "، ".join(escape_markdown(n) for n in failed_names[:5])
            report_lines.append(f"📋 القنوات الفاشلة: {failed_list}")

    report_text = "\n".join(report_lines)

    # تنظيف بيانات الجلسة
    _cleanup_user_data(context)

    await _safe_edit(
        query,
        report_text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 إرسال فتوى أخرى", callback_data="user_send_fatwa")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")],
        ]),
    )


def _cleanup_user_data(context: ContextTypes.DEFAULT_TYPE):
    """تنظيف جميع بيانات الجلسة الخاصة بالميزة."""
    for key in (
        USER_SF_SELECTED_KEY,
        USER_SF_CHANNELS_KEY,
        USER_SF_CHANNELS_TS_KEY,
        "user_sf_valid_channels",
        "user_sf_current_page",
    ):
        context.user_data.pop(key, None)


# ==================== Cancel ====================


async def user_send_fatwa_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العملية والعودة للقائمة الرئيسية."""
    query = update.callback_query
    await query.answer("تم الإلغاء")

    _cleanup_user_data(context)

    from core.utils import create_main_keyboard

    user_id = update.effective_user.id
    is_admin = bot_db.is_admin(user_id)

    await _safe_edit(
        query,
        "👋 مرحباً بك في بوت ادارة الفتاوى\n👇 القائمة الرئيسية:",
        reply_markup=create_main_keyboard(is_admin),
    )
