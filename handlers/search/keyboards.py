from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def create_search_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔍 بحث شامل", callback_data="search_all"), InlineKeyboardButton("🔢 بحث برقم الفتوى", callback_data="search_number")],
        [InlineKeyboardButton("🤖 بحث بالذكاء الاصطناعي", callback_data="search_ai"), InlineKeyboardButton("🎛️ بحث متقدم", callback_data="search_smart")],
        [InlineKeyboardButton("🔙 رجوع للقائمة الرئيسية", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def create_browse_keyboard():
    keyboard = [
        [InlineKeyboardButton("👤 العلماء", callback_data="search_scholar"), InlineKeyboardButton("🗂️ التصنيفات", callback_data="search_category")],
        [InlineKeyboardButton("📚 المصادر", callback_data="search_source"), InlineKeyboardButton("🎲 فتوى عشوائية", callback_data="random_fatwa")],
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
