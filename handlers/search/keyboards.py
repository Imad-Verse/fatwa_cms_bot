from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def create_search_keyboard():
    keyboard = [
        [InlineKeyboardButton("🏷️ بحث بالعنوان", callback_data="search_title"), InlineKeyboardButton("👤 بحث بالعالم", callback_data="search_scholar")],
        [InlineKeyboardButton("🗂️ بحث بالتصنيف", callback_data="search_category"), InlineKeyboardButton("📚 بحث بالمصدر", callback_data="search_source")],
        [InlineKeyboardButton("🔢 بحث برقم الفتوى", callback_data="search_number"), InlineKeyboardButton("🔍 بحث شامل", callback_data="search_all")],
        [InlineKeyboardButton("🎛️ بحث متقدم", callback_data="search_smart"), InlineKeyboardButton("🤖 بحث بالذكاء الاصطناعي", callback_data="search_ai")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_browse_keyboard():
    keyboard = [
        [InlineKeyboardButton("👤 العلماء", callback_data="search_scholar"), InlineKeyboardButton("🗂️ التصنيفات", callback_data="search_category")],
        [InlineKeyboardButton("📚 المصادر", callback_data="search_source"), InlineKeyboardButton("📅 أحدث الفتاوى", callback_data="search_latest")],
        [InlineKeyboardButton("🔥 الأكثر مشاهدة", callback_data="search_popular"), InlineKeyboardButton("🎲 فتوى عشوائية", callback_data="random_fatwa")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def _smart_label(label: str, selected: bool) -> str:
    return f"✅ {label}" if selected else label

def _build_smart_search_keyboard(state: dict) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(_smart_label('🏷️ العنوان', state.get('title')), callback_data='smart_toggle_title')],
        [InlineKeyboardButton(_smart_label('📝 المحتوى', state.get('text')), callback_data='smart_toggle_text')],
        [InlineKeyboardButton(_smart_label('👤 العالم', bool(state.get('scholars'))), callback_data='smart_select_scholar')],
        [
            InlineKeyboardButton('🔍 بحث الآن', callback_data='smart_search_now'),
            InlineKeyboardButton('🔙 رجوع', callback_data='smart_cancel')
        ],
    ]
    return InlineKeyboardMarkup(keyboard)
