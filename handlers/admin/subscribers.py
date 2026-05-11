import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone, time as dt_time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, 
    MessageHandler, CallbackQueryHandler, filters
)
from telegram.error import BadRequest

from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import OWNER_ID
from core.utils import (
    sanitize_input, create_main_keyboard, 
    back_to_categories_keyboard, escape_markdown, notify_new_subscription
)
from handlers.general import cancel_operation, start_refresh, back_to_main

logger = logging.getLogger(__name__)

# Singletons for Database Managers
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

async def manage_subscribers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode_override: str | None = None,
    page_override: int | None = None,
):
    """عرض قائمة المشتركين النشطين"""
    query = update.callback_query
    await query.answer()
    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return


    mode = "active"
    page = 0
    if mode_override in {"active", "inactive"}:
        mode = mode_override
        try:
            page = int(page_override) if page_override is not None else 0
        except (TypeError, ValueError):
            page = 0
    else:
        data = query.data
        if data.startswith("manage_subscribers_"):
            tail = data.replace("manage_subscribers_", "", 1)
            parts = [part for part in tail.split("_") if part]
            if parts:
                if parts[0] in {"active", "inactive"}:
                    mode = parts[0]
                    if len(parts) > 1:
                        try:
                            page = int(parts[1])
                        except (TypeError, ValueError):
                            page = 0
                else:
                    # توافق مع الصيغة القديمة: manage_subscribers_{page}
                    try:
                        page = int(parts[0])
                    except (TypeError, ValueError):
                        page = 0

    ITEMS_PER_PAGE = 10
    if mode == "inactive":
        total_count = await bot_db.get_inactive_users_count()
    else:
        total_count = await bot_db.get_active_users_count()

    total_pages = max((total_count - 1) // ITEMS_PER_PAGE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    offset = page * ITEMS_PER_PAGE

    if mode == "inactive":
        users = await bot_db.get_inactive_users(limit=ITEMS_PER_PAGE, offset=offset)
    else:
        users = await bot_db.get_active_users(limit=ITEMS_PER_PAGE, offset=offset)

    admin_ids = {int(a['user_id']) for a in await bot_db.get_admins()}
    admin_ids.add(int(OWNER_ID))

    if total_count == 0:
        title = "المستخدمون غير النشطين [0]" if mode == "inactive" else "المستخدمون النشطين [0]"
        toggle_mode = "active" if mode == "inactive" else "inactive"
        toggle_label = "✅ النشطة" if mode == "inactive" else "⚫ غير النشطة"
        await query.edit_message_text(
            f"🙍 <b>{title}</b>\n\nلا يوجد مستخدمون في هذه القائمة.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("⬅️ السابق", callback_data="noop"),
                    InlineKeyboardButton(toggle_label, callback_data=f"manage_subscribers_{toggle_mode}_0"),
                    InlineKeyboardButton("➡️ التالي", callback_data="noop"),
                ],
                [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
            ]),
            parse_mode='HTML'
        )
        return

    state_label = "غير النشطين" if mode == "inactive" else "النشطين"
    user_state = "غير نشط" if mode == "inactive" else "نشط"
    lines = [
        f"🙍 <b>المستخدمون {state_label} [{total_count}]</b>",
        f"الصفحة: {page + 1}/{total_pages}",
        "",
    ]

    for idx, user_row in enumerate(users, start=offset + 1):
        full_name = html.escape(user_row.get('full_name') or '-')
        username = f"@{html.escape(user_row['username'])}" if user_row.get('username') else "بدون يوزر"
        role = "أدمن" if int(user_row['user_id']) in admin_ids else "مستخدم"
        lines.append(
            f"{idx}. {full_name} | {username} | <code>{user_row['user_id']}</code> | {role} | {user_state}"
        )

    text = "\n".join(lines)
    prev_page_cb = f"manage_subscribers_{mode}_{page - 1}" if page > 0 else "noop"
    next_page_cb = f"manage_subscribers_{mode}_{page + 1}" if page < total_pages - 1 else "noop"
    toggle_mode = "active" if mode == "inactive" else "inactive"
    toggle_label = "✅ النشطة" if mode == "inactive" else "⚫ غير النشطة"
    keyboard = [[
        InlineKeyboardButton("⬅️ السابق", callback_data=prev_page_cb),
        InlineKeyboardButton(toggle_label, callback_data=f"manage_subscribers_{toggle_mode}_0"),
        InlineKeyboardButton("➡️ التالي", callback_data=next_page_cb),
    ]]
    if mode == "inactive" and total_count:
        keyboard.append([InlineKeyboardButton("🗑️ حذف غير النشطين (إزالة البوت)", callback_data="cleanup_subscribers")])
    keyboard.append([InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

# ==============================================================================
# 📂 القسم 4: إدارة التصنيفات والمواضيع (Categories & Topics)
# ==============================================================================



def _is_user_removed_error(error: Exception) -> bool:
    err = str(error).lower()
    removal_tokens = (
        "forbidden",
        "bot was blocked",
        "user is deactivated",
        "chat not found",
    )
    return any(token in err for token in removal_tokens)


async def cleanup_inactive_subscribers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clean up inactive subscribers that actually removed the bot."""
    query = update.callback_query
    await query.answer("جاري التنظيف...", cache_time=0)

    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    users = await bot_db.get_inactive_users()
    total_users = len(users)
    
    if total_users == 0:
        await query.answer("✅ لا يوجد مستخدمون غير نشطين لتنظيفهم.")
        return

    status_msg = await query.message.reply_text(f"⏳ جاري بدء تنظيف {total_users} مستخدم...")

    removed_count = 0
    reactivated_count = 0
    kept_count = 0

    for idx, user_row in enumerate(users):
        user_id = int(user_row["user_id"])
        try:
            # نحاول إرسال Chat Action للتحقق من أن المستخدم لم يحظر البوت
            await context.bot.send_chat_action(chat_id=user_id, action="typing")
            await bot_db.set_user_blocked(user_id, False)
            reactivated_count += 1
        except Exception as e:
            if _is_user_removed_error(e):
                if await bot_db.remove_user(user_id):
                    removed_count += 1
                else:
                    kept_count += 1
            else:
                kept_count += 1
                logger.debug(f"Skipping cleanup for user {user_id}; could not verify removal state: {e}")
        
        # تأخير بسيط لتجنب الـ Rate Limits
        await asyncio.sleep(0.05)
        
        # تحديث رسالة التقدم كل 50 مستخدم
        if (idx + 1) % 50 == 0:
            try:
                await status_msg.edit_text(
                    f"⏳ جاري التنظيف... ({idx + 1}/{total_users})\n🗑️ محذوف: {removed_count}\n🔄 مفعل: {reactivated_count}"
                )
            except Exception:
                pass

    await status_msg.edit_text(
        "✅ اكتمل تنظيف المستخدمين غير النشطين.\n\n"
        f"🗑️ المحذوف (حظر/تعطيل فعلي): {removed_count}\n"
        f"🔄 عاد للنشاط تلقائيًا: {reactivated_count}\n"
        f"📌 تم الإبقاء عليه (تعذر التحقق): {kept_count}"
    )

    await manage_subscribers(update, context, mode_override="inactive", page_override=0)


