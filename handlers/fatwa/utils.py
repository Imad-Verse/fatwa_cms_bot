import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from core.utils import safe_edit_message_text, safe_reply_text

logger = logging.getLogger(__name__)

# Constants for background data keys
_DELIVERY_LOG_KEY = "fatwa_delivery_log"
_PENDING_DUPLICATE_ANSWER_KEY = "pending_duplicate_answer"
_PENDING_DUPLICATE_MATCHES_KEY = "pending_duplicate_matches"

def _build_delivery_report_keyboard(fatwa_id: int) -> InlineKeyboardMarkup:
    """Keyboard for delivery results report."""
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
    """Register a sent message in bot_data for potential deletion later."""
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
    """Track progress in the fatwa addition conversation."""
    history = context.user_data.setdefault('add_step_history', [])
    if not history or history[-1] != step:
        history.append(step)
    context.user_data['add_step'] = step

def _pop_add_step(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """Go back one step in the history."""
    history = context.user_data.get('add_step_history', [])
    if len(history) <= 1:
        return None
    history.pop()
    return history[-1] if history else None

def _add_flow_nav_keyboard(include_back_step: bool = True) -> InlineKeyboardMarkup:
    """Standard navigation buttons for addition flow."""
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
    """Generic helper to send or edit messages during the addition flow."""
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
    """Constructs the correct 'Back' button based on navigation history."""
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

    return InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")

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
    """Helper to maintain view context across edits and navigation."""
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
