import logging
from telegram import Update, ChatMember
from telegram.ext import ContextTypes
from core.bot_db import BotDatabaseManager
from core.utils import notify_new_subscription

logger = logging.getLogger(__name__)
bot_db = BotDatabaseManager()

def _has_manage_messages_permission(chat_type: str, member: ChatMember) -> bool:
    status = getattr(member, "status", None)
    if status in (ChatMember.OWNER, ChatMember.ADMINISTRATOR):
        if chat_type == "channel":
            if hasattr(member, "can_post_messages"):
                return bool(member.can_post_messages)
            return True
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

async def track_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تتبع تحديثات حالة البوت في القنوات والمجموعات"""
    try:
        result = update.my_chat_member
        if not result: return

        chat = result.chat
        chat_type = 'channel' if chat.type == 'channel' else 'group' if chat.type in ['group', 'supergroup'] else None
        if not chat_type: return

        has_manage_perm = _has_manage_messages_permission(chat_type, result.new_chat_member)
        status_str = 'active' if has_manage_perm else 'inactive'

        if status_str == 'active':
            logger.info(f"Bot active in {chat_type}: {chat.title} ({chat.id})")
        else:
            logger.info(f"Bot inactive or missing permissions in {chat_type}: {chat.title} ({chat.id})")

        is_new = not await bot_db.channel_exists(chat.id)
        await bot_db.add_channel(chat.id, chat.title, chat.username, chat_type, status_str)

        if is_new and status_str == 'active':
             await notify_new_subscription(
                context.bot,
                chat_type,
                {'id': chat.id, 'name': chat.title, 'username': chat.username},
                context
            )
    except Exception as e:
        logger.error(f"Error in track_chat_member: {e}", exc_info=True)
