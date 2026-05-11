import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.utils import format_fatwa_card, back_to_main_keyboard

fatwa_db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()
logger = logging.getLogger(__name__)

async def toggle_favorite_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل حالة المفضلة للفتوى"""
    query = update.callback_query
    # toggle_fav_{id}
    fatwa_id = int(query.data.split('_')[-1])
    user_id = update.effective_user.id

    is_added = await bot_db.toggle_favorite(user_id, fatwa_id)
    try:
        await fatwa_db.increment_favorites_count(fatwa_id, 1 if is_added else -1)
    except Exception as e:
        # Keep favorite action stable even if count update fails.
        logger.debug(f"increment_favorites_count failed for fatwa {fatwa_id}: {e}")

    # تحديث نص الزر فقط دون إعادة تحميل الرسالة كاملة إذا أمكن
    # لكن في التيليجرام يجب إعادة إرسال reply_markup

    # نحصل على لوحة المفاتيح الحالية
    current_markup = query.message.reply_markup
    new_keyboard = []

    for row in current_markup.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data == query.data:
                # هذا هو زر المفضلة، نغير نصه والكول باك
                new_text = "❌ حذف من المفضلة" if is_added else "⭐ مفضلة"
                # نلاحظ أن الكول باك يبقى نفسه لأننا نعكس الحالة عند الضغط
                new_row.append(InlineKeyboardButton(new_text, callback_data=query.data))
            else:
                new_row.append(btn)
        new_keyboard.append(new_row)

    msg = "✅ تمت الإضافة للمفضلة" if is_added else "❌ تمت الإزالة من المفضلة"

    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
    except Exception as e:
        # إذا لم يتغير المحتوى (ضغطتين سريعتين)، نتجاهل الخطأ مع تسجيل تشخيصي.
        logger.debug(f"edit_message_reply_markup skipped for fav toggle {fatwa_id}: {e}")

    await query.answer(msg)

async def my_favorites_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المفضلة"""
    query = update.callback_query
    await query.answer()

    page = 0
    data = query.data or ""
    sort_mode = context.user_data.get("favorites_sort", "recent")

    if data.startswith("fav_sort_"):
        sort_mode = data.split("_")[-1]
        if sort_mode not in ("recent", "views"):
            sort_mode = "recent"
        context.user_data["favorites_sort"] = sort_mode
        page = 0
    elif data.startswith("fav_page_"):
        page = int(data.split('_')[-1])
    else:
        context.user_data.setdefault("favorites_sort", sort_mode)

    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE
    user_id = update.effective_user.id

    favorite_rows = await bot_db.get_user_favorites(user_id)
    favorite_added_at = {
        int(row["fatwa_id"]): (row.get("created_at") or "")
        for row in favorite_rows
    }
    all_fav_ids = [int(row["fatwa_id"]) for row in favorite_rows]
    all_favorites = await fatwa_db.get_fatwas_by_ids(all_fav_ids, public_only=True)
    if sort_mode == "views":
        def _views_key(f):
            return (
                int(f.get("views") or 0),
                favorite_added_at.get(int(f.get("id", 0)), ""),
                int(f.get("fatwa_number") or f.get("id", 0) or 0),
            )

        all_favorites.sort(key=_views_key, reverse=True)
    total_count = len(all_favorites)
    favorites = all_favorites[offset:offset + ITEMS_PER_PAGE]

    if not favorites and page == 0:
        await query.edit_message_text(
            "⭐ **المفضلة**\n\nلا توجد فتاوى في المفضلة حالياً.",
            reply_markup=back_to_main_keyboard(),
            parse_mode='Markdown'
        )
        return

    sort_label = "الأحدث" if sort_mode == "recent" else "الأكثر مشاهدة"
    text = f"⭐ **المفضلة** (صفحة {page + 1})\nعدد الفتاوى: {total_count}\nالترتيب: {sort_label}\n\n"
    keyboard = []

    sort_row = [
        InlineKeyboardButton("🆕 الأحدث" + (" ✅" if sort_mode == "recent" else ""), callback_data="fav_sort_recent"),
        InlineKeyboardButton("👁️ الأكثر مشاهدة" + (" ✅" if sort_mode == "views" else ""), callback_data="fav_sort_views")
    ]
    keyboard.append(sort_row)

    for fatwa in favorites:
        keyboard.append([InlineKeyboardButton(f"🔸 {fatwa['title']}", callback_data=f"view_{fatwa['id']}_fav_{page}")])

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"fav_page_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"fav_page_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def top_favorites_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض أكثر 5 فتاوى تفضيلاً (Global Favorites)"""
    query = update.callback_query
    await query.answer()

    top_favs = await fatwa_db.get_top_favorites(5)

    keyboard = []

    if top_favs:
        text = "🌟 **الفتاوى الأكثر تفضيلاً**\n\nاختر فتوى لعرضها:"
        for fav in top_favs:
            # fav: {'id': ..., 'title': ..., 'fav_count': ...}
            btn_text = f"{fav['fav_count']}❤️ | {fav['title']}"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_{fav['id']}_topfav")])
    else:
        text = "🌟 **الفتاوى الأكثر تفضيلاً**\n\nلا توجد بيانات كافية حالياً."

    keyboard.append([InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
