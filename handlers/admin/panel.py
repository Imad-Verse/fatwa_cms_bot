
"""
Administrative Handlers (handlers/admin.py)
------------------------------------------
Contains all administrative functions for the Fatwa Bot:
- Admin Panel & Statistics.
- Admin Management (Add/Remove).
- Categorization (Categories & Topics).
- Scholar Management.
- Publishing Settings (Auto-publish, Job scheduling).
- Broadcast (Podcast).
- Database Backup.
"""

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
from core.config import OWNER_ID, BotState
from core.utils import (
    sanitize_input, create_main_keyboard, 
    back_to_categories_keyboard, escape_markdown, notify_new_subscription
)
from handlers.general import cancel_operation, start_refresh, back_to_main

logger = logging.getLogger(__name__)

# Singletons for Database Managers
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# ==============================================================================
# 🛠️ القسم 1: لوحة التحكم والوصول الأساسي
# ==============================================================================

async def test_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test notification sending to owner"""
    user_id = update.effective_user.id
    if not await bot_db.is_admin(user_id):
        return

    await update.message.reply_text("⏳ جاري إرسال إشعار تجريبي...")
    try:
        await notify_new_subscription(
            context.bot,
            'user',
            {'id': 12345, 'name': 'مستخدم تجريبي', 'username': 'test_user'},
            context
        )
        await update.message.reply_text("✅ تم إرسال الإشعار! تحقق من رسائلك.")
    except Exception as e:
        await update.message.reply_text(f"❌ فشل الإرسال: {e}")

# ==================== لوحة الإدارة ====================

async def _build_admin_panel_payload() -> tuple[str, InlineKeyboardMarkup]:
    """بناء نص وأزرار لوحة الإدارة مع حالة وضع الصيانة."""
    maintenance_enabled = (await bot_db.get_setting("maintenance_mode", "0") == "1")
    maintenance_btn = "🟢 وضع الصيانة" if maintenance_enabled else "🔴 وضع الصيانة"
    maintenance_status = "🟢 مفعّل" if maintenance_enabled else "🔴 غير مفعّل"

    keyboard = [
        [InlineKeyboardButton("➕ إضافة فتوى", callback_data="add_fatwa")],
        [InlineKeyboardButton("📝 المسودات", callback_data="admin_drafts"), InlineKeyboardButton("🔄 المكررة", callback_data="admin_duplicates")],
        [InlineKeyboardButton("🏷️ إدارة التصنيفات", callback_data="manage_categories"), InlineKeyboardButton("👤 إدارة العلماء", callback_data="manage_scholars")],
        [InlineKeyboardButton("🔗 إدارة الروابط", callback_data="manage_links"), InlineKeyboardButton("📚 إدارة المصادر", callback_data="manage_sources")],
        [InlineKeyboardButton("⚙️ إدارة النشر التلقائي", callback_data="auto_publish_panel"), InlineKeyboardButton("⏱️ إعدادات الجدولة", callback_data="admin_settings")],
        [InlineKeyboardButton("👥 إدارة المشتركين", callback_data="manage_subscribers"), InlineKeyboardButton("📢 إدارة القنوات", callback_data="manage_channels")],
        [InlineKeyboardButton("🎙️ بودكاست", callback_data="podcast_panel"), InlineKeyboardButton("🧑‍💼 إدارة المسؤولين", callback_data="manage_admins")],
        [InlineKeyboardButton(maintenance_btn, callback_data="toggle_maintenance_mode"), InlineKeyboardButton("💾 نسخة احتياطية", callback_data="backup_db")],
        [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]
    ]

    text = (
        f"⚙️ **لوحة الإدارة**\n"
        f"حالة وضع الصيانة: {maintenance_status}\n\n"
        "اختر من العمليات التالية:"
    )
    return text, InlineKeyboardMarkup(keyboard)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة الإدارة"""
    query = update.callback_query
    await query.answer()

    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    text, markup = await _build_admin_panel_payload()
    await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')


async def toggle_maintenance_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبديل وضع الصيانة وتشغيله/إيقافه."""
    query = update.callback_query

    if not await bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ هذا القسم للمسؤولين فقط", show_alert=True)
        return

    current = (await bot_db.get_setting("maintenance_mode", "0") == "1")
    new_value = "0" if current else "1"
    await bot_db.set_setting("maintenance_mode", new_value)

    if new_value == "1":
        await query.answer("🟢 تم تفعيل وضع الصيانة")
    else:
        await query.answer("🔴 تم إيقاف وضع الصيانة")

    text, markup = await _build_admin_panel_payload()
    await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')


async def show_admin_drafts(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int | None = None):
    """عرض المسودات مع أزرار التحكم"""
    query = update.callback_query
    await query.answer()

    if page is None:
        page = 0
        data = query.data
        if "admin_drafts_" in data:
            page = int(data.split("_")[-1])

    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    drafts, total_drafts = await db.get_all_fatwas(status='draft', limit=ITEMS_PER_PAGE, offset=offset)

    # stats = db.get_statistics()
    # total_drafts = stats.get('draft_fatwas', 0)

    if not drafts and page == 0:
        await query.edit_message_text(
            "✅ لا توجد مسودات حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
        return

    text = f"📝 **المسودات** (صفحة {page + 1})\nإجمالي المسودات: {total_drafts}\n\n"
    keyboard = []

    for fatwa in drafts:
        # Title Button (acts as Label or View)
        keyboard.append([InlineKeyboardButton(f"🔸 {fatwa['title']}", callback_data=f"view_{fatwa['id']}_drafts_{page}")])

        # Action Buttons: Publish, View (Edit is inside View)
        btns = [
            InlineKeyboardButton("📢 نشر", callback_data=f"publish_{fatwa['id']}_drafts_{page}"),
            InlineKeyboardButton("👁️ معاينة", callback_data=f"view_{fatwa['id']}_drafts_{page}")
        ]
        keyboard.append(btns)

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_drafts_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_drafts:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"admin_drafts_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def show_duplicates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الفتاوى المكررة"""
    query = update.callback_query
    await query.answer()

    page = 0
    data = query.data or ""
    if data.startswith("admin_duplicates_"):
        try:
            page = max(0, int(data.rsplit("_", 1)[-1]))
        except ValueError:
            page = 0

    ITEMS_PER_PAGE = 5
    offset = page * ITEMS_PER_PAGE

    try:
        duplicates, total_count = await asyncio.gather(
            db.get_duplicate_fatwas(ITEMS_PER_PAGE, offset),
            db.get_duplicate_count(),
        )
    except Exception:
        logger.exception("Failed to load duplicate fatwas")
        await query.edit_message_text(
            "❌ تعذر تحميل قائمة الفتاوى المكررة حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
        return

    if not duplicates and page == 0:
        await query.edit_message_text(
            "✅ لا توجد فتاوى مكررة حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
        return

    text = f"🔄 **الفتاوى المكررة (نفس الجواب)** (صفحة {page + 1})\nإجمالي المكرر: {total_count}\n\n"
    keyboard = []

    for fatwa in duplicates:
        # Title Button (acts as Label or View)
        keyboard.append([InlineKeyboardButton(f"🔸 {fatwa['title']}", callback_data=f"view_{fatwa['id']}_dups_{page}")])

        # Action Buttons
        btns = [
             InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_fatwa_{fatwa['id']}_dups_{page}"),
             InlineKeyboardButton("🗑️ حذف", callback_data=f"confirm_delete_{fatwa['id']}_dups_{page}")
        ]
        keyboard.append(btns)

    # Navigation
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"admin_duplicates_{page-1}"))
    if offset + ITEMS_PER_PAGE < total_count:
        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"admin_duplicates_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# ==============================================================================
# 👥 القسم 2: إدارة المسؤولين (Admins)
# ==============================================================================

async def manage_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسؤولين مباشرة مع أزرار الإدارة"""
    await list_admins_handler(update, context)

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """طلب بيانات المسؤول الجديد (ID أو username)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ **إضافة مسؤول**\n\nأرسل `User ID` أو `@username` للمستخدم:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="admin_panel")]]),
    )
    return STATE_ADMIN_ADD

async def receive_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    target_user_id = None
    target_username = None

    if text.isdigit():
        target_user_id = int(text)
        if target_user_id <= 0:
            await update.message.reply_text("❌ الآيدي غير صالح.")
            return STATE_ADMIN_ADD
    else:
        username_candidate = text[1:] if text.startswith("@") else text
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username_candidate):
            await update.message.reply_text("❌ أدخل `User ID` صحيح أو `@username` صحيح.", parse_mode="Markdown")
            return STATE_ADMIN_ADD

        user_row = await bot_db.get_user_by_username(username_candidate)
        if user_row:
            target_user_id = int(user_row["user_id"])
            target_username = user_row.get("username") or username_candidate
        else:
            try:
                chat = await context.bot.get_chat(f"@{username_candidate}")
                if getattr(chat, "type", "") != "private":
                    await update.message.reply_text("❌ هذا اليوزر لا يشير إلى حساب مستخدم خاص.")
                    return STATE_ADMIN_ADD
                target_user_id = int(chat.id)
                target_username = chat.username or username_candidate
            except Exception:
                await update.message.reply_text(
                    "❌ تعذر العثور على هذا اليوزر.\nتأكد من صحة اليوزر وأن المستخدم بدأ البوت أولاً."
                )
                return STATE_ADMIN_ADD

    if await bot_db.add_admin(target_user_id, target_username):
        if target_username:
            success_text = f"✅ تم إضافة المسؤول: @{html.escape(target_username)} | <code>{target_user_id}</code>"
        else:
            success_text = f"✅ تم إضافة المسؤول: <code>{target_user_id}</code>"
        await update.message.reply_text(
            success_text,
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
    else:
        await update.message.reply_text(
            "⚠️ المستخدم مسؤول بالفعل.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 لوحة الإدارة", callback_data="admin_panel")]])
        )
    return ConversationHandler.END

async def list_admins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسؤولين"""
    query = update.callback_query
    await query.answer()

    admins = await bot_db.get_admins()
    admins_sorted = sorted(
        admins,
        key=lambda row: (
            0 if int(row.get('user_id', 0)) == int(OWNER_ID) else 1,
            int(row.get('user_id', 0)),
        ),
    )
    title = f"الأدمن [{len(admins_sorted)}]"
    lines = [f"🧑‍💼 <b>{title}</b>", ""]

    if not admins_sorted:
        lines.append("لا يوجد أدمن مسجل حالياً.")
    else:
        for idx, row in enumerate(admins_sorted, start=1):
            username = f"@{html.escape(row['username'])}" if row.get('username') else "بدون يوزر"
            role = "المالك" if int(row.get('user_id', 0)) == int(OWNER_ID) else "أدمن"
            lines.append(f"{idx}. {username} | <code>{row['user_id']}</code> | {role} | نشط")

    text = "\n".join(lines)
    keyboard = [
        [
            InlineKeyboardButton("❌ حذف", callback_data="remove_admin"),
            InlineKeyboardButton("➕ إضافة", callback_data="add_admin"),
        ],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")],
    ]
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

# ==================== إدارة المسؤولين ====================

async def start_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة المسؤولين لحذف أحدهم."""
    query = update.callback_query
    await query.answer()
    
    admins = await bot_db.get_admins()
    # استبعاد المالك من قائمة الحذف
    admins_to_remove = [a for a in admins if int(a['user_id']) != int(OWNER_ID)]
    
    if not admins_to_remove:
        await query.edit_message_text(
            "⚠️ لا يوجد مسؤولون آخرون لحذفهم.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_admins")]])
        )
        return ConversationHandler.END

    keyboard = []
    for a in admins_to_remove:
        name = f"@{a['username']}" if a['username'] else f"ID: {a['user_id']}"
        keyboard.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"del_adm_{a['user_id']}")])
    
    keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data="manage_admins")])
    
    await query.edit_message_text(
        "🗑️ **حذف مسؤول**\nاختر المسؤول الذي تريد حذفه:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return STATE_ADMIN_REMOVE

async def handle_remove_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة حذف المسؤول عند الضغط على زر الحذف."""
    query = update.callback_query
    await query.answer()
    
    target_id = int(query.data.split("_")[-1])
    if await bot_db.remove_admin(target_id):
        await query.edit_message_text(
            f"✅ تم حذف المسؤول (ID: {target_id}) بنجاح.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_admins")]])
        )
    else:
        await query.edit_message_text(
            "❌ فشل حذف المسؤول أو أنه غير موجود.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="manage_admins")]])
        )
    return ConversationHandler.END

admin_conv = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(start_add_admin, pattern='^add_admin$'),
        CallbackQueryHandler(start_remove_admin, pattern='^remove_admin$')
    ],
    states={
        BotState.STATE_ADMIN_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_id)],
        BotState.STATE_ADMIN_REMOVE: [CallbackQueryHandler(handle_remove_admin_callback, pattern='^del_adm_')]
    },
    fallbacks=[
        CallbackQueryHandler(admin_panel, pattern='^admin_panel$'),
        CallbackQueryHandler(manage_admins, pattern='^manage_admins$'),
        CommandHandler('cancel', admin_panel)
    ]
)


# ==============================================================================
# 🎓 القسم 3: إدارة العلماء (Scholars)
# ==============================================================================

