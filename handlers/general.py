"""
معالجات عامة (handlers/general.py)
----------------------------------
يحتوي على:
- أمر البداية /start
- أمر المساعدة /help
- معالجة الأخطاء
- إلغاء العمليات
"""

import logging
import os
import asyncio
from urllib.parse import quote_plus
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, ApplicationHandlerStop
from core.config import TELEGRAM_TOKEN, OWNER_ID
from core.bot_db import BotDatabaseManager
from core.utils import (
    rate_limiter, monitor, create_main_keyboard, 
    back_to_main_keyboard, notify_new_subscription,
    safe_reply_text, safe_edit_message_text
)

logger = logging.getLogger(__name__)
db = BotDatabaseManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    try:
        user = update.effective_user
        user_id = user.id

        # Save User to DB (Async)
        if not await db.user_exists(user_id):
            await db.add_user(user_id, user.username, user.full_name)
            # Send notification in background to avoid blocking the start response
            asyncio.create_task(notify_new_subscription(
                context.bot, 
                'user', 
                {'id': user_id, 'name': user.full_name, 'username': user.username}, 
                context
            ))
        else:
            await db.add_user(user_id, user.username, user.full_name)

        # Deep Linking for Fatwa (by number or id)
        if context.args and context.args[0].startswith('fatwa_'):
            try:
                fatwa_token = context.args[0].split('_')[1]
                if not fatwa_token.isdigit():
                    raise ValueError("Invalid fatwa token")
                fatwa_number = int(fatwa_token)
                from core.database import FatwaDatabaseManager
                fatwa_db = FatwaDatabaseManager()
                fatwa = await fatwa_db.get_fatwa_by_number(fatwa_number)
                if not fatwa:
                    fatwa = await fatwa_db.get_fatwa(fatwa_number)
                if not fatwa:
                    raise ValueError("Fatwa not found")
                from handlers.fatwa.view import send_fatwa_message
                await send_fatwa_message(update, context, fatwa['id'])
                return ConversationHandler.END
            except Exception as e:
                logger.error(f"Deep link error: {e}")
                # Fallback to normal start if error

        # Rate limiting
        if not rate_limiter.is_allowed(user_id):
            await safe_reply_text(
                update,
                "⏳ لقد تجاوزت الحد المسموح من الطلبات. يرجى الانتظار دقيقة.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 إعادة المحاولة", callback_data="start_refresh")]])
            )
            return ConversationHandler.END

        # تسجيل في المراقبة
        monitor.log_command('start', user_id)

        # التحقق من الصلاحيات (Async)
        is_admin = await db.is_admin(user_id)

        welcome_text = (
            f"👋 مرحباً بك {user.first_name if user.first_name else ''} في بوت ادارة الفتاوى\n\n"
            "🔎 بوابتك الموثوقة للعلم الشرعي\n"
            "نسعى لتقريب العلم وتسهيل الوصول إلى فتاوى كبار العلماء بأسلوب ميسر ومنظم.\n\n"
            "📚 ماذا يمكنك أن تفعل؟\n"
            "• البحث في آلاف الفتاوى المؤرشفة.\n"
            "• تصفح الفتاوى حسب العالم أو التصانيف.\n"
            "• الاستماع للمواد الصوتية المرفقة (إن وجدت).\n"
            "• متابعة جديد الفتاوى والأكثر انتشاراً.\n"
            "• النشر التلقائي للفتاوى يوميًا في المجموعات والقنوات التي أضافت البوت مشرفًا\n\n"
            "👇 اختر من القائمة أدناه للبدء:"
        )

        # القائمة السفلية (Persistent Menu)
        reply_keyboard = [["🤖 بوتاتنا", "🏠 القائمة الرئيسية"]]
        markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)

        await safe_reply_text(update, "👇 القائمة السريعة:", reply_markup=markup)

        await safe_reply_text(
            update,
            welcome_text,
            reply_markup=create_main_keyboard(is_admin)
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error in start: {e}")
        await error_handler(update, context)

async def start_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحديث القائمة الرئيسية (callback)"""
    query = update.callback_query
    await query.answer("جاري التحديث...")

    user_id = update.effective_user.id
    is_admin = await db.is_admin(user_id)

    try:
        await safe_edit_message_text(
            query,
            f"👋 مرحباً بك {update.effective_user.first_name if update.effective_user.first_name else ''} في بوت ادارة الفتاوى\n👇 القائمة الرئيسية:",
            reply_markup=create_main_keyboard(is_admin)
        )
    except Exception as e:
        # أحياناً التعديل يفشل إذا المحتوى نفسه (Message is not modified)
        logger.debug(f"start_refresh edit_message_text skipped: {e}")

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء العملية الحالية"""
    user_id = update.effective_user.id
    is_admin = await db.is_admin(user_id)

    if update.callback_query:
        await update.callback_query.answer("تم الإلغاء")
        await safe_edit_message_text(
            update.callback_query,
            "❌ تم إلغاء العملية.",
            reply_markup=create_main_keyboard(is_admin)
        )
    else:
        await safe_reply_text(
            update,
            "❌ تم إلغاء العملية.",
            reply_markup=create_main_keyboard(is_admin)
        )
    return ConversationHandler.END

async def maintenance_mode_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إيقاف تفاعل المستخدمين غير المسؤولين أثناء وضع الصيانة."""
    if not update:
        return

    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or chat.type != "private":
        return

    if await db.is_admin(user.id):
        return
    
    # Check setting from DB (Async)
    if await db.get_setting("maintenance_mode", "0") != "1":
        return

    maintenance_text = (
        "🚧 البوت في وضع الصيانة حاليًا.\n"
        "يرجى المحاولة لاحقًا."
    )
    # Use a fixed developer contact URL or fetch from config
    developer_url = "https://t.me/abulharith_imad" 
    maintenance_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 راسل المطور", url=developer_url)]
    ])

    if update.callback_query:
        try:
            await update.callback_query.answer("🚧 البوت في وضع الصيانة", show_alert=False)
        except Exception:
            pass
        if update.callback_query.message:
            await safe_reply_text(update.callback_query.message, maintenance_text, reply_markup=maintenance_markup)
    elif update.effective_message:
        await safe_reply_text(update.effective_message, maintenance_text, reply_markup=maintenance_markup)

    raise ApplicationHandlerStop

async def our_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة بوتاتنا"""
    text = (
        "هذه قائمة البوتات الخاصة بنا:\n\n"
        "1- 🤖 **إدارة الفتاوى | Fatwa CMS:**\n"
        "مشروع خيري يهدف لأرشفة ونشر فتاوى العلماء الثقات، وتسهيل الوصول إليها عبر التليجرام.\n\n"
        "🎯 **ماذا يمكنه أن يفعل لك؟**\n"
        "• محرك بحث سريع ودقيق.\n"
        "• تصنيف موضوعي شامل.\n"
        "• دعم الوسائط المتعددة (نص، صوت، روابط).\n"
        "• إمكانية النشر التلقائي للقنوات والمجموعات.\n\n"
        "🔥 جربه الآن: @Fatwa\\_CMS\\_Bot\n\n"
        "2- 🤖 **العملاق للمستندات | Titan Pdf Pro :**\n\n"
        "🎯 **ماذا يمكنه أن يفعل لك؟**\n\n"
        "بوت متخصص في معالجة ملفات PDF والصور والنصوص، يقوم بالعديد من المهام:\n"
        "• تحويل الصور إلى PDF والعكس\n"
        "• دمج وتقسيم ملفات PDF\n"
        "• حماية وضغط الملفات\n"
        "• إضافة علامات مائية والمزيد\n\n"
        "🔥 جربه الآن: @TitanPdfBot\n\n"
        "3- 🤖 **العملاق للتحميل | Titan Downloader :**\n\n"
        "🎯 **ماذا يمكنه أن يفعل لك؟**\n\n"
        "يتيح لك تحميل فيديوهاتك المفضلة بأعلى جودة ممكنة، بالإضافة إلى استخراج الصوت منها بكل سهولة وسرعة، حيث يُعتبر سريعًا ومجانيًا وسهل الاستخدام ✅️. يمكنه تحميل الفيديوهات القصيرة من منصات مثل يوتيوب، تيك توك، فيسبوك، وإنستجرام.\n\n"
        "⛔️ **تنبيه:** لا تستخدم البوت فيما يغضب الله عز وجل كتحميل الموسيقى والصور المحرمة!\n\n"
        "🔥 جربه الآن: @TitanSvBot\n\n"
    )
    # أزرار مدمجة
    keyboard = [
        [
            InlineKeyboardButton("📩 مراسلة المطور", url="https://t.me/abulharith_imad"),
            InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message_text(update.callback_query, text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await safe_reply_text(update, text, reply_markup=reply_markup, parse_mode='Markdown')

async def help_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المساعدة والمعلومات"""
    text = (
        "ℹ️ **عن بوت ادارة الفتاوى**\n\n"
        "مشروع خيري يهدف لأرشفة ونشر فتاوى العلماء الثقات، وتسهيل الوصول إليها عبر التليجرام.\n\n"
        "🎯 **أهدافنا:**\n"
        "• نشر العلم الشرعي الصحيح من مصادره الموثوقة.\n"
        "• توفير مرجع سهل وسريع لطالب العلم والعامة.\n"
        "• استغلال التقنية في خدمة الدين.\n\n"
        "🔥 **المزايا:**\n"
        "• محرك بحث سريع ودقيق.\n"
        "• تصنيف موضوعي شامل.\n"
        "• دعم الوسائط المتعددة (نص، صوت، روابط).\n"
        "• إمكانية النشر التلقائي للقنوات والمجموعات.\n\n"
        "📮 **للتواصل والاقتراحات:**\n"
        "نسعد بتلقي ملاحظاتكم لتطوير البوت عبر حساب الدعم الفني."
    )
    bot_username = context.bot.username or "Fatwa_CMS_Bot"
    bot_url = f"https://t.me/{bot_username}"
    share_text = (
        "🤖بوت إدارة الفتاوى | Fatwa CMS:\n"
        "مشروع خيري يهدف لأرشفة ونشر فتاوى العلماء الثقات، وتسهيل الوصول إليها عبر التليجرام.\n\n"
        "🎯 ماذا يمكنه أن يفعل لك؟\n"
        "• محرك بحث سريع ودقيق.\n"
        "• تصنيف موضوعي شامل.\n"
        "• دعم الوسائط المتعددة (نص، صوت، روابط).\n"
        "• إمكانية النشر التلقائي للقنوات والمجموعات.\n\n"
        "🔥 جربه الآن 👇"
    )
    share_url = f"https://t.me/share/url?url={quote_plus(bot_url)}&text={quote_plus(share_text)}"

    keyboard = [
        [
            InlineKeyboardButton("📩 راسل المطور", url="https://t.me/abulharith_imad"),
            InlineKeyboardButton("\U0001f4e4 \u0645\u0634\u0627\u0631\u0643\u0629 \u0627\u0644\u0628\u0648\u062a", url=share_url),
        ],
        [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")],
    ]

    if update.callback_query:
        await update.callback_query.answer()
        await safe_edit_message_text(update.callback_query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await safe_reply_text(update, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def how_to_add_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تعليمات إضافة البوت للقنوات والمجموعات"""
    query = update.callback_query
    await query.answer()

    # محاولة جلب يوزر المالك
    owner_username = "Abulharith_imad" # Default fallback
    try:
        owner_chat = await context.bot.get_chat(OWNER_ID)
        if owner_chat.username:
            owner_username = owner_chat.username
    except Exception as e:
        logger.warning(f"Failed to fetch owner info: {e}")

    # Escape usernames for Markdown
    owner_username_esc = owner_username.replace('_', '\\_')

    text = (
        "**طريقة إضافة البوت إلى قناتك أو مجموعتك**\n\n"
        "للاستفادة من خدمة نشر الفتاوى التلقائية:\n\n"
        "**أولًا:**\n"
        "قم بإضافة الروبوت @Fatwa\\_CMS\\_Bot إلى قناتك أو مجموعتك بصفة **مشرف**، وذلك عبر أحد الروابط التالية:\n\n"
        "🔗 **لإضافته إلى مجموعة (Group):**\n"
        "[اضغط هنا للإضافة](https://t.me/Fatwa_CMS_Bot?startgroup&admin=delete_messages)\n\n"
        "📢 **لإضافته إلى قناة (Channel):**\n"
        "[اضغط هنا للإضافة](https://t.me/Fatwa_CMS_Bot?startchannel&admin=post_messages+edit_messages+delete_messages)\n\n"
        "🔒 **تنبيه مهم:**\n"
        "ستظهر صلاحيات إدارة الرسائل مفعلة افتراضيًا، ويمكنك إلغاء أي صلاحية قبل الضغط على (إضافة مشرف) إذا رغبت.\n\n"
        "الفوائد المنشورة موثوقة ومنقولة عن كبار العلماء، لذا يُرجى عدم حذف الروبوت حتى تستمر الخدمة دون انقطاع.\n\n"
        "🌸 **للمساعدة:**\n"
        "إذا ما عرفت كيفية إضافة البوت لقناتك او مجموعتك تواصل معي:\n"
        f"👉 @{owner_username_esc}\n\n"
        "أو شاهد الفيديو التوضيحي عبر الضغط على الزر الموجود أسفل\n"
        "نسأل الله أن ينفع بها، ويجعلها في ميزان الحسنات."
    )

    keyboard = [
        [InlineKeyboardButton("🎥 فيديو توضيحي", callback_data="show_add_bot_tutorial")],
        [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]
    ]

    await safe_edit_message_text(query, text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown', disable_web_page_preview=True)

async def show_add_bot_tutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال فيديو وشرح لطريقة إضافة البوت"""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id

    # Paths to media
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets_dir = os.path.join(base_dir, "assets")
    video_path = os.path.join(assets_dir, "add_bot_Help_video.mp4")
    image_path = os.path.join(assets_dir, "add_bot_Help_img.jpg")

    media = []
    opened_files = []
    caption_text = "فيديو توضيحي لطريقة اضافة بوت ادارة الفتاوى لقناتك أو مجموعتك\n\n🤖 إدارة الفتاوى | @Fatwa_CMS_Bot"

    if os.path.exists(video_path):
        from telegram import InputMediaVideo
        video_file = open(video_path, "rb")
        opened_files.append(video_file)
        media.append(InputMediaVideo(media=video_file, caption=caption_text))
        caption_text = None # Only attach caption to the first item

    if os.path.exists(image_path):
        from telegram import InputMediaPhoto
        image_file = open(image_path, "rb")
        opened_files.append(image_file)
        media.append(InputMediaPhoto(media=image_file, caption=caption_text))

    try:
        if media:
            try:
                 await context.bot.send_media_group(chat_id=chat_id, media=media)
            except Exception as e:
                logger.error(f"Error sending media group: {e}")
                await query.message.reply_text("❌ حدث خطأ أثناء إرسال الوسائط.")
        else:
            await safe_reply_text(query.message, "❌ عذراً، الملفات التوضيحية غير متوفرة حالياً.")
    finally:
        for f in opened_files:
            try:
                f.close()
            except Exception:
                pass

    # Main Menu Button
    keyboard = [[InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="start_refresh")]]
    await context.bot.send_message(
        chat_id=chat_id,
        text="👇 القائمة الرئيسية:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """زر وهمي لعناصر فارغة."""
    if update.callback_query:
        await update.callback_query.answer("لا توجد عناصر لعرضها.", show_alert=False)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الأخطاء العامة"""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    monitor.log_error()

    try:
        if isinstance(update, Update) and update.effective_message:
            text = "⚠️ حدث خطأ غير متوقع. يرجى المحاولة لاحقًا."
            if update.callback_query:
                await safe_reply_text(update.callback_query.message, text)
            else:
                await safe_reply_text(update.effective_message, text)
    except Exception as notify_error:
        logger.warning(f"Failed to notify user about internal error: {notify_error}")

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرجوع للقائمة الرئيسية"""
    await start_refresh(update, context)
    return ConversationHandler.END
