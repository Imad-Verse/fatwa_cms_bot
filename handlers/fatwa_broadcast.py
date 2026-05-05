import logging
import asyncio
from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import RetryAfter, Forbidden

from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.keyboards import create_published_fatwa_keyboard
from handlers.fatwa_utils import _register_delivery_message, _build_delivery_report_keyboard

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

async def process_broadcast_task(
    application,
    fatwa_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    admin_id: int,
    target_channels: list,
    target_groups: list,
    users: list,
    maintenance_enabled: bool,
    status_message_id: int = None
) -> None:
    """المهمة الخلفية لمعالجة الإرسال الجماعي مع التعامل مع قيود التليجرام."""
    report = {
        'channels': {'success': 0, 'fail': 0},
        'groups': {'success': 0, 'fail': 0},
        'subscribers': {'success': 0, 'fail': 0},
    }
    sent_to = set()
    total_targets = len(target_channels) + len(target_groups) + len(users)
    processed = 0

    async def send_with_retry(chat_id, category_key, is_chat_dict=False):
        nonlocal processed
        if chat_id in sent_to:
            return
        
        for attempt in range(3):
            try:
                sent_msg = await application.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                
                # Mock context for registration
                class MockContext:
                    def __init__(self, app): self.application = app
                
                _register_delivery_message(MockContext(application), fatwa_id, chat_id, sent_msg.message_id)
                sent_to.add(chat_id)
                report[category_key]['success'] += 1
                break
            except RetryAfter as e:
                logger.warning(f"Flood limit hit, sleeping for {e.retry_after}s")
                await asyncio.sleep(e.retry_after)
                continue
            except Forbidden as e:
                report[category_key]['fail'] += 1
                err = str(e)
                if is_chat_dict:
                    bot_db.update_channel_status(chat_id, 'inactive')
                elif chat_id != admin_id:
                    bot_db.set_user_blocked(chat_id, True)
                break
            except Exception as e:
                if attempt == 2:
                    report[category_key]['fail'] += 1
                    logger.error(f"Failed to send to {chat_id} after 3 attempts: {e}")
                await asyncio.sleep(0.1)

        processed += 1
        # تحديث الحالة كل 20 رسالة إذا كان هناك عدد كبير
        if status_message_id and total_targets > 20 and processed % 20 == 0:
            try:
                progress_text = f"⏳ جارٍ الإرسال... ({processed}/{total_targets})"
                await application.bot.edit_message_text(
                    chat_id=admin_id,
                    message_id=status_message_id,
                    text=progress_text
                )
            except Exception:
                pass
        
        # تأخير بسيط لتجنب التزاحم
        await asyncio.sleep(0.05)

    # تنفيذ الإرسال
    for ch in target_channels:
        await send_with_retry(ch['chat_id'], 'channels', is_chat_dict=True)
    for grp in target_groups:
        await send_with_retry(grp['chat_id'], 'groups', is_chat_dict=True)
    for user_id in users:
        await send_with_retry(user_id, 'subscribers', is_chat_dict=False)

    # تحديث عدد المشاهدات بناءً على النجاح في القنوات والمجموعات
    approx_views = report['channels']['success'] + report['groups']['success']
    if approx_views > 0:
        db.increment_views_by(fatwa_id, approx_views)

    # إعداد ملخص النهاية
    if maintenance_enabled:
        summary = (
            "🛠️ **وضع الصيانة مفعّل**\n\n"
            "تم إرسال الفتوى للمدير فقط.\n"
            f"✅ نجاح الإرسال: {report['subscribers']['success']}\n"
            f"❌ فشل الإرسال: {report['subscribers']['fail']}"
        )
    else:
        summary = (
            "✅ **اكتمل الإرسال الجماعي**\n\n"
            f"📢 **القنوات:** {report['channels']['success']} (فشل: {report['channels']['fail']})\n"
            f"👥 **المجموعات:** {report['groups']['success']} (فشل: {report['groups']['fail']})\n"
            f"👤 **المشتركون:** {report['subscribers']['success']} (فشل: {report['subscribers']['fail']})"
        )

    try:
        if status_message_id:
            await application.bot.edit_message_text(
                chat_id=admin_id,
                message_id=status_message_id,
                text=summary,
                parse_mode='Markdown',
                reply_markup=_build_delivery_report_keyboard(fatwa_id)
            )
        else:
            await application.bot.send_message(
                chat_id=admin_id,
                text=summary,
                parse_mode='Markdown',
                reply_markup=_build_delivery_report_keyboard(fatwa_id)
            )
    except Exception as e:
        logger.error(f"Failed to send final broadcast summary: {e}")

async def broadcast_fatwa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية إرسال الفتوى في الخلفية للجهات المشتركة."""
    from core.utils import build_fatwa_preview_text

    query = update.callback_query
    
    if not bot_db.is_admin(update.effective_user.id):
        await query.answer("❌ غير مصرح لك.", show_alert=True)
        return

    fatwa_id = int(query.data.split('_')[1])
    fatwa = db.get_fatwa(fatwa_id)

    if not fatwa:
        await query.answer("❌ الفتوى غير موجودة.", show_alert=True)
        return

    await query.answer("🚀 بدأت عملية الإرسال في الخلفية...", show_alert=False)

    maintenance_enabled = (bot_db.get_setting("maintenance_mode", "0") == "1")
    admin_id = int(update.effective_user.id)

    if maintenance_enabled:
        target_channels = []
        target_groups = []
        users = [admin_id]
    else:
        all_channels = bot_db.get_channels(status='active')
        users = list(dict.fromkeys(bot_db.get_active_user_ids()))
        target_channels = [ch for ch in all_channels if ch['type'] == 'channel']
        target_groups = [ch for ch in all_channels if ch['type'] in ['group', 'supergroup']]

    text_to_send, is_long = build_fatwa_preview_text(fatwa, max_length=3600)

    reply_markup = create_published_fatwa_keyboard(
        fatwa=fatwa,
        bot_username=context.bot.username,
        is_long=is_long,
    )

    if not (target_channels or target_groups or users):
        await query.answer("⚠️ لا توجد جهات إرسال نشطة حاليًا.", show_alert=True)
        return

    # إرسال رسالة حالة أولية للمدير
    status_msg = await query.message.reply_text(
        f"⏳ جارٍ بدء الإرسال الجماعي للفتوى رقم {fatwa.get('fatwa_number', fatwa_id)}..."
    )

    # تشغيل المهمة في الخلفية
    asyncio.create_task(
        process_broadcast_task(
            application=context.application,
            fatwa_id=fatwa_id,
            text=text_to_send,
            reply_markup=reply_markup,
            admin_id=admin_id,
            target_channels=target_channels,
            target_groups=target_groups,
            users=users,
            maintenance_enabled=maintenance_enabled,
            status_message_id=status_msg.message_id
        )
    )
