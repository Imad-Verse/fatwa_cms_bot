import asyncio
import logging
import html
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import ContextTypes
from core.bot_db import BotDatabaseManager
from core.utils import safe_reply_text, safe_edit_message_text
from .utils import _ensure_admin, _is_bot_removed_chat_error

logger = logging.getLogger(__name__)
bot_db = BotDatabaseManager()

async def manage_channels_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة إدارة القنوات"""
    query = update.callback_query
    if query: await query.answer()

    if not await bot_db.is_admin(update.effective_user.id):
        if query: await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    keyboard = [
        [InlineKeyboardButton("📢 حالة القنوات", callback_data="status_channels"), InlineKeyboardButton("👥 حالة المجموعات", callback_data="status_groups")],
        [InlineKeyboardButton("🔙 رجوع للوحة الإدارة", callback_data="admin_panel")]
    ]
    await safe_edit_message_text(
        query,
        "📢 **إدارة القنوات والمجموعات**\n\nتحكم في القنوات المضافة والنشر التلقائي.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def show_channel_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة القنوات/المجموعات"""
    query = update.callback_query
    if not await _ensure_admin(update, query): return
    target_type = 'channel' if 'channels' in query.data else 'group'
    await list_channels_handler(update, context, c_type_override=target_type, status_override='active', page_override=0)

async def list_channels_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    c_type_override: Optional[str] = None,
    status_override: Optional[str] = None,
    page_override: Optional[int] = None,
):
    """سرد القنوات (نشطة/غير نشطة)"""
    query = update.callback_query
    if query: await query.answer()

    if not await bot_db.is_admin(update.effective_user.id):
        if query: await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    if c_type_override and status_override is not None:
        c_type, status, page = c_type_override, status_override, page_override if page_override is not None else 0
    else:
        parts = query.data.split('_')
        if len(parts) < 3: return
        c_type, status = parts[1], parts[2]
        if c_type not in {"channel", "group"} or status not in {"active", "inactive"}: return
        try: page = int(parts[3]) if len(parts) >= 4 else 0
        except: page = 0

    try:
        channels = await bot_db.get_channels(status=status, chat_type=c_type)
        channels.sort(key=lambda row: (str(row.get('added_at') or ''), int(row.get('chat_id', 0))), reverse=True)
    except Exception as e:
        logger.error(f"Error fetching channels: {e}")
        error_msg = "❌ حدث خطأ أثناء تحميل القنوات."
        if query: await safe_edit_message_text(query, error_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 إدارة القنوات", callback_data="manage_channels")]]))
        return

    ITEMS_PER_PAGE = 10; total_count = len(channels)
    total_pages = max((total_count - 1) // ITEMS_PER_PAGE + 1, 1)
    page = max(0, min(page, total_pages - 1))
    start = page * ITEMS_PER_PAGE
    page_items = channels[start:start + ITEMS_PER_PAGE]

    is_channel = c_type == 'channel'
    icon = "📢" if is_channel else "👥"
    entity_label = "القنوات المشتركة" if is_channel else "المجموعات المشتركة"
    state_label = "النشطة" if status == 'active' else "غير النشطة"

    lines = [f"{icon} <b>{entity_label} {state_label} [{total_count}]</b>", f"الصفحة: {page + 1}/{total_pages}", ""]
    if not page_items: lines.append("لا توجد عناصر في هذه القائمة.")
    else:
        item_state = "نشطة" if status == 'active' else "غير نشطة"
        for idx, ch in enumerate(page_items, start=start + 1):
            title = html.escape(ch.get('title') or '-')
            username = f"@{html.escape(ch['username'])}" if ch.get('username') else "بدون يوزر"
            lines.append(f"{idx}. {title} | {username} | <code>{ch['chat_id']}</code> | {item_state}")

    text = "\n".join(lines)
    # Navigation Rows
    prev_page_cb = f"list_{c_type}_{status}_{page - 1}" if page > 0 else "noop"
    next_page_cb = f"list_{c_type}_{status}_{page + 1}" if page < total_pages - 1 else "noop"
    toggle_label = "⚫ غير النشطة" if status == 'active' else "✅ النشطة"
    toggle_status = "inactive" if status == 'active' else "active"

    nav_row = [
        InlineKeyboardButton("⬅️ السابق", callback_data=prev_page_cb),
        InlineKeyboardButton(toggle_label, callback_data=f"list_{c_type}_{toggle_status}_0"),
        InlineKeyboardButton("التالي ➡️", callback_data=next_page_cb)
    ]

    keyboard = [nav_row] # Top

    if status == 'inactive' and total_count:
        keyboard.append([InlineKeyboardButton("🗑️ حذف الكل (خروج)", callback_data=f"cleanup_{c_type}")])
    
    keyboard.append(nav_row) # Bottom
    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="manage_channels")])
    
    await safe_reply_text(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

async def cleanup_inactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove inactive channels/groups only when the bot actually left."""
    query = update.callback_query; await query.answer("جاري التنظيف...", cache_time=0)
    if not await _ensure_admin(update, query): return
    c_type = query.data.split('_')[1]
    if c_type not in {"channel", "group"}: return
    channels = await bot_db.get_channels(status='inactive', chat_type=c_type)
    removed, kept, reactivated = 0, 0, 0
    total = len(channels)
    
    if total == 0:
        await query.answer("✅ لا توجد قنوات/مجموعات غير نشطة لتنظيفها.")
        return

    status_msg = await query.message.reply_text(f"⏳ جاري بدء تنظيف {total} من القنوات/المجموعات...")

    for idx, ch in enumerate(channels):
        chat_id, should_remove = ch['chat_id'], False
        try:
            member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if member.status in (ChatMember.LEFT, ChatMember.KICKED): should_remove = True
            else:
                from .tracking import _has_manage_messages_permission
                new_status = 'active' if _has_manage_messages_permission(c_type, member) else 'inactive'
                if ch.get('status') != new_status: await bot_db.update_channel_status(chat_id, new_status)
                if new_status == 'active': reactivated += 1
                else: kept += 1
        except Exception as e:
            if _is_bot_removed_chat_error(e): should_remove = True
            else: kept += 1
        
        if should_remove:
            if await bot_db.remove_channel(chat_id): removed += 1
            try: await context.bot.leave_chat(chat_id)
            except: pass
            
        # تأخير لتجنب الضغط
        await asyncio.sleep(0.05)
        
        # تحديث التقدم
        if (idx + 1) % 10 == 0:
            try:
                await status_msg.edit_text(f"⏳ جاري التنظيف... ({idx + 1}/{total})\n🗑️ محذوف: {removed}\n🔄 نشط: {reactivated}")
            except: pass

    await status_msg.edit_text(f"✅ اكتمل تنظيف غير النشطة.\n🗑️ المحذوف: {removed}\n🔄 عاد للنشاط: {reactivated}\n📌 الإبقاء عليه: {kept}")
    await list_channels_handler(update, context, c_type_override=c_type, status_override='inactive', page_override=0)
