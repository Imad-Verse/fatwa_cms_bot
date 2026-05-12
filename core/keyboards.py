from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def create_main_keyboard(is_admin=False):
    """إنشاء لوحة المفاتيح الرئيسية"""
    keyboard = [
        [InlineKeyboardButton("🔍 بحث عن فتوى", callback_data="search_fatwas"), InlineKeyboardButton("📖 مطالعة الفتاوى", callback_data="browse_fatwas")],
        [InlineKeyboardButton("🔥 الأكثر مشاهدة", callback_data="search_popular"), InlineKeyboardButton("📅 أحدث الفتاوى", callback_data="search_latest")],
        [InlineKeyboardButton("⭐ مفضلتك", callback_data="my_favorites"), InlineKeyboardButton("🌟 المفضلة", callback_data="top_favorites")],
        [InlineKeyboardButton("➕ أضفه إلى قناتك أو مجموعتك", callback_data="how_to_add_bot"), InlineKeyboardButton("📨 ارسل فتوى لقناتك", callback_data="user_send_fatwa")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"), InlineKeyboardButton("ℹ️ حول البوت", callback_data="help_info")],
    ]

    if is_admin:
        # لوحة الإدارة
        keyboard.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])

    # أزرار معلومات (Removed, merged above)
    # info_row = [ ... ]
    # keyboard.append(info_row)

    return InlineKeyboardMarkup(keyboard)


# ==========================================
# 🔙 دوال مساعدة للرجوع (Back Keyboards)
# ==========================================

def back_to_main_keyboard(label: str = "\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629") -> InlineKeyboardMarkup:
    """
    لوحة قياسية للرجوع إلى القائمة الرئيسية (back_main)

    لا تغيّر منطق العمل: ما زال callback_data = "back_main"
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="back_main")]])


def back_to_search_keyboard(text="🔙 رجوع للبحث"):
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data="search_fatwas")]])

def create_pagination_keyboard(
    current_page: int,
    total_pages: int,
    callback_prefix: str,
    back_button: InlineKeyboardButton | None = None,
    extra_buttons: list = None
) -> InlineKeyboardMarkup:
    """
    Generic pagination keyboard builder.

    Args:
        current_page: 0-indexed current page.
        total_pages: Total number of pages.
        callback_prefix: Prefix for pagination callback (e.g., 'res_page').
        back_button: Optional 'Back' button to append.
        extra_buttons: Optional list of extra buttons to append.
    """
    keyboard = []
    nav_row = []

    if current_page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"{callback_prefix}_{current_page - 1}"))

    # Show page number (optional, maybe non-clickable)
    # nav_row.append(InlineKeyboardButton(f"{current_page + 1}/{total_pages}", callback_data="noop"))

    if current_page < total_pages - 1:
        nav_row.append(InlineKeyboardButton("➡️ التالي", callback_data=f"{callback_prefix}_{current_page + 1}"))

    if nav_row:
        keyboard.append(nav_row)

    if extra_buttons:
        for btn_row in extra_buttons:
            # Ensure it's a list of lists or normalize it
            if isinstance(btn_row, list):
                keyboard.append(btn_row)
            else:
                keyboard.append([btn_row])

    if back_button:
        keyboard.append([back_button])

    return InlineKeyboardMarkup(keyboard)

def create_fatwa_card_keyboard(fatwa_id: int, fatwa_number: int) -> list:
    """Create standard buttons for a fatwa card in a list."""
    return [InlineKeyboardButton(f"📖 عرض فتوى #{fatwa_number}", callback_data=f"view_{fatwa_id}_search")]

def create_published_fatwa_keyboard(
    fatwa: dict,
    bot_username: str | None,
    is_long: bool,
    continue_label: str = "📖 قراءة الفتوى كاملة",
) -> InlineKeyboardMarkup:
    """Create the keyboard used when a fatwa is sent to subscribers/channels."""
    keyboard = []
    bot_username = (bot_username or "Fatwa_CMS_Bot").lstrip("@")
    fatwa_id = fatwa["id"]

    if is_long:
        fatwa_num = fatwa.get("fatwa_number", fatwa_id)
        deep_link = f"https://t.me/{bot_username}?start=fatwa_{fatwa_num}"
        keyboard.append([InlineKeyboardButton(continue_label, url=deep_link)])

    link_buttons = []
    if fatwa.get("source_url"):
        link_buttons.append(InlineKeyboardButton("📚 الانتقال للمصدر", url=fatwa["source_url"]))
    if fatwa.get("audio_url"):
        link_buttons.append(InlineKeyboardButton("🎧 سماع الصوتية", url=fatwa["audio_url"]))
    if link_buttons:
        keyboard.append(link_buttons)

    keyboard.append([
        InlineKeyboardButton("📋 نسخ الفتوى", callback_data=f"copy_full_{fatwa_id}"),
        InlineKeyboardButton("🤖 بوت إدارة الفتاوى", url=f"https://t.me/{bot_username}"),
    ])

    return InlineKeyboardMarkup(keyboard)

def create_fatwa_view_keyboard(
    fatwa: dict,
    is_admin: bool,
    is_favorite: bool,
    back_button: InlineKeyboardButton | None = None,
    context_suffix: str = "",
    continue_reading_callback_data: str | None = None,
    random_callback_data: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Create the full keyboard for viewing a fatwa.
    """
    keyboard = []
    fatwa_id = fatwa['id']
    suffix = f"_{context_suffix}" if context_suffix else ""

    # 1. Source Links
    link_buttons = []
    if fatwa.get('source_url'):
        link_buttons.append(InlineKeyboardButton("📚 الانتقال للمصدر", url=fatwa['source_url']))
    if fatwa.get('audio_url'):
        link_buttons.append(InlineKeyboardButton("🎧 سماع الصوتية", url=fatwa['audio_url']))
    if link_buttons:
        keyboard.append(link_buttons)

    if continue_reading_callback_data:
        keyboard.append([InlineKeyboardButton("⬇️ متابعة القراءة", callback_data=continue_reading_callback_data)])

    if random_callback_data:
        keyboard.append([InlineKeyboardButton("🎲 فتوى أخرى", callback_data=random_callback_data)])

    # 2. User Actions (Favorite / Report)
    action_buttons = []
    fav_text = "❌ حذف من المفضلة" if is_favorite else "⭐ مفضلة"
    action_buttons.append(InlineKeyboardButton(fav_text, callback_data=f"toggle_fav_{fatwa_id}"))

    # Report Link
    from urllib.parse import quote
    report_msg = f"السلام عليكم ورحمة الله وبركاته\nأريد الابلاغ عن فتوى التي تحمل رقم: {fatwa.get('fatwa_number', fatwa_id)}"
    report_url = f"https://t.me/abulharith_imad?text={quote(report_msg)}"
    action_buttons.append(InlineKeyboardButton("⚠️ إبلاغ", url=report_url))
    keyboard.append(action_buttons)

    # 2.5 Related (same scholar + related)
    related_btn = InlineKeyboardButton("🔗 ذات الصلة", callback_data=f"related_fatwas_{fatwa_id}")
    scholar_id = fatwa.get('scholar_id')
    if scholar_id:
        keyboard.append([
            InlineKeyboardButton("📚 فتاوى الشيخ", callback_data=f"scholar_fatwas_{scholar_id}_{fatwa_id}"),
            related_btn
        ])
    else:
        keyboard.append([related_btn])

    # 3. Admin Actions
    if is_admin:
        # Edit / Delete
        keyboard.append([
            InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa_id}{suffix}"),
            InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa_id}{suffix}")
        ])

        # Publish / Broadcast
        pub_row = []
        if fatwa.get('status') == 'draft':
            pub_row.append(InlineKeyboardButton("📢 نشر الآن", callback_data=f"publish_{fatwa_id}{suffix}"))
        pub_row.append(InlineKeyboardButton("📢 إرسال الفتوى", callback_data=f"broadcast_{fatwa_id}"))
        keyboard.append(pub_row)

    # 4. Copy / Back
    nav_row = [InlineKeyboardButton("📋 نسخ النص", callback_data=f"copy_full_{fatwa_id}")]
    if back_button:
        nav_row.append(back_button)
    else:
        nav_row.append(InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main"))

    keyboard.append(nav_row)

    return InlineKeyboardMarkup(keyboard)
