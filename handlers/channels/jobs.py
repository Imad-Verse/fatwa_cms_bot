import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
from telegram.ext import ContextTypes
from telegram import ChatMember
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.keyboards import create_published_fatwa_keyboard
from core.utils import build_fatwa_preview_text, escape_markdown, split_long_message, safe_send_message
from .utils import (
    _register_delivery_message, _build_delivery_report_keyboard
)
from .autopublish import (
    _get_scheduled_fatwa, _clear_scheduled_fatwa, _get_selected_publish_category, _load_targeted_topic_selection
)

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

async def daily_fatwa_job(context: ContextTypes.DEFAULT_TYPE, force: bool = False, respect_scheduled: bool = True, trigger_admin_id: int = None):
    """مهمة النشر اليومي أو النشر الفوري اليدوي."""
    random_enabled = await bot_db.get_setting('auto_publish', '0') == '1'
    specific_enabled = await bot_db.get_setting('auto_publish_specific', '0') == '1'
    maintenance_enabled = await bot_db.get_setting('maintenance_mode', '0') == '1'
    target_cat_id, target_topic_ids, fatwa = None, [], None
    scheduled_used, mode_label = False, "نشر فوري يدوي" if force else "نشر عشوائي"

    if respect_scheduled:
        num, fatwa = await _get_scheduled_fatwa()
        if fatwa:
            scheduled_used, mode_label = True, "جدولة فتوى لمرة واحدة"
            logger.info(f"Scheduled fatwa detected: #{num}. Proceeding with scheduled publish.")

    if fatwa is None and not force and not random_enabled and not specific_enabled:
        logger.debug("No scheduled fatwa and auto-publish is disabled. Skipping job.")
        return

    if fatwa is None:
        logger.info("No scheduled fatwa found. Selecting a random fatwa for auto-publish.")
        if specific_enabled:
            target_cat_id, _ = await _get_selected_publish_category()
            if not target_cat_id:
                logger.warning("Specific publish enabled but no category selected. Skipping.")
                return
            target_topic_ids, _ = await _load_targeted_topic_selection(target_cat_id); mode_label = "نشر محدد"
        fatwa = await db.get_random_published_fatwa(category_id=target_cat_id, topic_ids=target_topic_ids if target_topic_ids else None)
        if not fatwa:
            logger.warning("No published fatwas found for selection. Skipping.")
            return

    try:
        text, is_long = build_fatwa_preview_text(fatwa, max_length=3600)
        markup = create_published_fatwa_keyboard(fatwa=fatwa, bot_username=context.bot.username, is_long=is_long, continue_label="تابع القراءة")
        channels = [] if maintenance_enabled else await bot_db.get_channels(status='active')
        sent_to = set(); count_ch, count_us, count_ad = 0, 0, 0

        for ch in channels:
            try:
                if ch['chat_id'] in sent_to: continue
                sent_msg = await context.bot.send_message(chat_id=ch['chat_id'], text=text, reply_markup=markup)
                _register_delivery_message(context, fatwa['id'], ch['chat_id'], sent_msg.message_id); sent_to.add(ch['chat_id']); count_ch += 1
                await asyncio.sleep(0.05) # Delay
            except Exception as e:
                logger.error(f"Failed to auto-publish to {ch['chat_id']}: {e}")
                if "Forbidden" in str(e) or "chat not found" in str(e).lower(): await bot_db.update_channel_status(ch['chat_id'], 'inactive')

        if count_ch > 0: await db.increment_views_by(fatwa['id'], count_ch)
        users = [] if maintenance_enabled else list(dict.fromkeys(await bot_db.get_active_user_ids()))
        for uid in users:
            try:
                if uid in sent_to: continue
                sent_msg = await context.bot.send_message(chat_id=uid, text=text, reply_markup=markup)
                _register_delivery_message(context, fatwa['id'], uid, sent_msg.message_id); sent_to.add(uid); count_us += 1
                await asyncio.sleep(0.05) # Delay
            except Exception as e:
                err_str = str(e).lower()
                if any(x in err_str for x in ("forbidden", "blocked", "deactivated", "chat not found")):
                    await bot_db.set_user_blocked(uid, True)
                logger.debug(f"Failed to send daily fatwa to user {uid}: {e}")

        admins_to_notify = []
        if maintenance_enabled:
            if trigger_admin_id:
                admins_to_notify = [{'user_id': trigger_admin_id}]
            else:
                admins_to_notify = await bot_db.get_admins()
        elif not users:
            admins_to_notify = await bot_db.get_admins()

        for admin in admins_to_notify:
            try:
                aid = int(admin['user_id'])
                if aid in sent_to: continue
                sent_msg = await context.bot.send_message(chat_id=aid, text=text, reply_markup=markup)
                _register_delivery_message(context, fatwa['id'], aid, sent_msg.message_id); sent_to.add(aid); count_ad += 1
            except Exception as e:
                logger.error(f"Failed to notify admin {admin.get('user_id')}: {e}")

        total = count_ch + count_us + count_ad
        report = (f"{'🛠️' if maintenance_enabled else '✅'} **تقرير النشر التلقائي اليومي**\n\n"
                  f"{'وضع الصيانة: **مفعّل**' if maintenance_enabled else ''}\n"
                  f"نوع النشر: **{mode_label}**\n\n📜 **الفتوى المنشورة:**\n"
                  f"رقم: `{fatwa.get('fatwa_number', fatwa['id'])}`\n"
                  f"العنوان: {escape_markdown(fatwa['title'])}\n\n"
                  f"📊 **إحصائيات:**\n📢 القنوات: {count_ch}\n👤 المشتركين: {count_us}\n🧑‍💼 المسؤولون: {count_ad}\n✅ المجموع: {total}")

        # إرسال التقرير النهائي للمسؤولين
        all_admins = await bot_db.get_admins()
        report_targets = [{'user_id': trigger_admin_id}] if maintenance_enabled and trigger_admin_id else all_admins

        for admin in report_targets:
            try:
                await context.bot.send_message(
                    chat_id=admin['user_id'],
                    text=report,
                    parse_mode='Markdown',
                    reply_markup=_build_delivery_report_keyboard(fatwa['id'])
                )
            except: pass
    finally:
        if scheduled_used: await _clear_scheduled_fatwa()

async def weekly_fatwa_report_job(context: ContextTypes.DEFAULT_TYPE):
    """Send a weekly report to users with new fatwas per scholar."""
    try:
        now_utc = datetime.utcnow(); now_local = datetime.now().astimezone()
        cfg_wd = context.bot_data.get("weekly_report_weekday")
        if cfg_wd is not None and now_local.weekday() != int(cfg_wd): return

        last_run_raw = await bot_db.get_setting('weekly_report_last_run', ''); start_utc = now_utc - timedelta(days=7)
        if last_run_raw:
            try:
                last_run = datetime.fromisoformat(last_run_raw)
                if last_run.date() == now_utc.date(): return
                start_utc = last_run
            except: pass

        start_ts = start_utc.strftime("%Y-%m-%d %H:%M:%S"); start_label, end_label = start_utc.strftime("%Y-%m-%d"), now_utc.strftime("%Y-%m-%d")
        counts = await db.get_new_fatwa_counts_by_scholar_since(start_ts); total = sum(i.get('count', 0) for i in counts)

        if total == 0: text = f"📚 تقرير الفتاوى الأسبوعي\n\nالفترة: {start_label} إلى {end_label}\nلا توجد فتاوى جديدة."
        else:
            lines = ["📚 تقرير الفتاوى الأسبوعي", "", f"الفترة: {start_label} إلى {end_label}", f"إجمالي الفتاوى الجديدة: {total}", ""]
            for i in counts: lines.append(f"- {i.get('scholar_name') or 'غير محدد'}: {i.get('count', 0)} فتوى جديدة")
            text = "\n".join(lines)

        parts = split_long_message(text); users = list(dict.fromkeys(await bot_db.get_active_user_ids()))
        if not users: await bot_db.set_setting('weekly_report_last_run', now_utc.isoformat()); return

        for uid in users:
            try:
                for part in parts: 
                    await safe_send_message(context.bot, uid, part)
                    await asyncio.sleep(0.05) # Delay
            except Exception as e:
                if any(x in str(e).lower() for x in ("forbidden", "blocked", "deactivated")): await bot_db.set_user_blocked(uid, True)
        await bot_db.set_setting('weekly_report_last_run', now_utc.isoformat())
    except Exception as e: logger.error(f"Weekly report job failed: {e}")
