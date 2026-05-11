import logging
from typing import List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import ContextTypes
from telegram.error import BadRequest
from core.bot_db import BotDatabaseManager

logger = logging.getLogger(__name__)
bot_db = BotDatabaseManager()

_DELIVERY_LOG_KEY = "fatwa_delivery_log"

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
    if not app: return
    store = app.bot_data.setdefault(_DELIVERY_LOG_KEY, {})
    fatwa_store = store.setdefault(str(int(fatwa_id)), {})
    chat_key = str(int(chat_id))
    msg_list = fatwa_store.setdefault(chat_key, [])
    msg_id = int(message_id)
    if msg_id not in msg_list:
        msg_list.append(msg_id)
        if len(msg_list) > 200: del msg_list[:-200]

async def _ensure_admin(update: Update, query=None) -> bool:
    user = update.effective_user
    is_admin = bool(user and await bot_db.is_admin(user.id))
    if is_admin: return True
    if query:
        try: await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        except Exception:
            if query.message: await query.message.reply_text("❌ هذا القسم للمسؤولين فقط")
    elif update.message: await update.message.reply_text("❌ هذا القسم للمسؤولين فقط")
    return False

async def _safe_edit_message_text(query, text: str, **kwargs):
    """Edit message text and ignore 'Message is not modified' errors."""
    try: await query.edit_message_text(text, **kwargs)
    except BadRequest as e:
        if "Message is not modified" in str(e): return
        raise

def _is_bot_removed_chat_error(error: Exception) -> bool:
    err = str(error).lower()
    removal_tokens = ("chat not found", "bot was kicked", "bot is not a member", "user not found")
    return any(token in err for token in removal_tokens)

def _parse_int_list_setting(raw_value: str) -> List[int]:
    if not raw_value: return []
    try: return [int(x.strip()) for x in raw_value.split(',') if x.strip()]
    except (ValueError, TypeError): return []

def _serialize_int_list_setting(int_list: List[int]) -> str:
    if not int_list: return ""
    return ",".join(str(x) for x in sorted(set(int_list)))
