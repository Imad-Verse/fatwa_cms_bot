"""
وحدة الأدوات والخدمات العامة (core/utils.py)
---------------------------------------
تحتوي على الدوال المساعدة والأنظمة الفرعية للبوت:
1. الأنظمة: التخزين المؤقت، مراقبة الأداء، وتحديد معدل الطلبات.
2. التنسيق: تنظيف النصوص وتجهيز رسائل الماركدوان (Markdown).
3. العمليات: إرسال وتحرير الرسائل بشكل آمن مع معالجة الأخطاء.
"""

import os
import re
import unicodedata
import sys
import socket
import asyncio
import time
import threading
import logging
from collections import defaultdict
from datetime import datetime
from functools import wraps
from telegram.error import NetworkError, TimedOut, TelegramError, BadRequest
from telegram import InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# ==========================================
# 🛡️ أنظمة التحسين (Cache, Monitor, RateLimit)
# ==========================================

class CacheManager:
    """نظام التخزين المؤقت لتحسين الأداء"""
    def __init__(self):
        self.cache = {}
        self.lock = threading.Lock()

    def get(self, key, ttl=300):
        """جلب قيمة من التخزين المؤقت"""
        with self.lock:
            if key in self.cache:
                value, timestamp = self.cache[key]
                if time.time() - timestamp < ttl:
                    return value
            return None

    def set(self, key, value):
        """حفظ قيمة في التخزين المؤقت"""
        with self.lock:
            self.cache[key] = (value, time.time())

    def delete(self, key):
        """حذف قيمة من التخزين المؤقت"""
        with self.lock:
            if key in self.cache:
                del self.cache[key]

    def delete_pattern(self, pattern: str):
        """حذف المفاتيح التي تحتوي على نمط معين"""
        with self.lock:
            keys_to_delete = [k for k in self.cache.keys() if pattern in k]
            for k in keys_to_delete:
                del self.cache[k]

    def clear(self):
        with self.lock:
            self.cache.clear()

def cached_async(ttl=300):
    """
    ديكوريتور لتخزين نتائج الدوال غير المتزامنة (Async) مؤقتاً.
    
    Args:
        ttl: مدة صلاحية التخزين بالثواني (الافتراضي 5 دقائق).
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # إنشاء مفتاح فريد بناءً على اسم الدالة والوسائط
            # نستخدم str() للوسائط لتبسيط الأمر، مع العلم أنها قد تكون غير قابلة للـ hash أحياناً
            key = f"{func.__name__}:{args}:{kwargs}"
            
            cached_val = cache.get(key, ttl=ttl)
            if cached_val is not None:
                return cached_val
            
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result
        return wrapper
    return decorator

class BotMonitor:
    """نظام مراقبة أداء البوت"""
    def __init__(self):
        self.metrics = {
            'messages_processed': 0,
            'errors': 0,
            'commands_executed': defaultdict(int),
            'users_active': set(),
            'start_time': datetime.now()
        }

    def log_command(self, command: str, user_id: int):
        """تسجيل أمر تم تنفيذه"""
        self.metrics['messages_processed'] += 1
        self.metrics['users_active'].add(user_id)
        self.metrics['commands_executed'][command] += 1

    def log_error(self):
        """تسجيل خطأ"""
        self.metrics['errors'] += 1

    def get_metrics(self) -> dict:
        """جلب المقاييس الحالية"""
        return {
            **self.metrics,
            'uptime': str(datetime.now() - self.metrics['start_time']),
            'unique_users': len(self.metrics['users_active'])
        }

class RateLimiter:
    """نظام التحكم في معدل الطلبات (للرسائل والأوامر العامة)"""
    def __init__(self, max_requests: int = 15, period: int = 60):
        self.max_requests = max_requests
        self.period = period
        self.requests = defaultdict(list)
        self.lock = threading.Lock()

    def is_allowed(self, user_id: int) -> bool:
        """التحقق إذا كان المستخدم يمكنه تنفيذ طلب جديد"""
        with self.lock:
            current_time = time.time()
            user_requests = self.requests[user_id]

            # إزالة الطلبات القديمة
            user_requests = [
                req_time for req_time in user_requests
                if current_time - req_time < self.period
            ]

            if len(user_requests) >= self.max_requests:
                return False

            user_requests.append(current_time)
            self.requests[user_id] = user_requests
            return True


class CallbackGuard:
    """
    حارس بسيط لمنع الضغط المتكرر السريع على نفس الزر (CallbackQuery)

    لا يغيّر منطق الأزرار، فقط يتجاهل النقرات المتتابعة خلال فترة قصيرة
    لتقليل الضغط على SQLite ومنع مفعول "التجمّد" الظاهري.
    """
    def __init__(self, min_interval: float = 1.5):
        self.min_interval = min_interval
        self.last_pressed = defaultdict(dict)  # {user_id: {data: timestamp}}
        self.lock = asyncio.Lock()

    async def is_fast_repeat(self, user_id: int, data: str, interval: float = None) -> bool:
        """
        يعيد True إذا كان الضغط مكرراً خلال فترة أقل من interval
        وإلا يقوم بتسجيل الوقت الحالي ويعيد False.
        """
        now = time.time()
        check_interval = interval if interval is not None else self.min_interval
        async with self.lock:
            user_map = self.last_pressed[user_id]
            last_time = user_map.get(data, 0)
            if now - last_time < check_interval:
                return True
            user_map[data] = now
            return False

# إنشاء نسخ من الأنظمة
cache = CacheManager()
monitor = BotMonitor()
rate_limiter = RateLimiter(max_requests=15, period=60)
_global_callback_guard_manager = CallbackGuard(min_interval=1.5)

def callback_guard(min_interval: float = 1.5):
    """
    ديكوريتور لمنع الضغط المتكرر السريع على نفس الزر.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            query = update.callback_query
            if not query:
                return await func(update, context, *args, **kwargs)
            
            user_id = update.effective_user.id
            data = query.data or ""
            
            # استخدام الحارس العالمي مع التمرير المباشر للـ interval
            if await _global_callback_guard_manager.is_fast_repeat(user_id, data, interval=min_interval):
                try:
                    await query.answer("⏳ يرجى الانتظار قليلاً...", show_alert=False)
                except Exception:
                    pass
                return
            
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

# ==========================================
# 🔒 نظام القفل (Singleton Lock - Socket Strategy)
# ==========================================
from core.config import LOCK_PORT

class SingletonLock:
    """
    نظام قفل باستخدام منفذ الشبكة (Socket) لضمان تشغيل نسخة واحدة فقط.
    أكثر موثوقية من ملف PID.
    """
    def __init__(self, port=LOCK_PORT):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(0.1) # timeout قصير

    def acquire(self) -> bool:
        """
        محاولة حجز المنفذ.
        يعيد True إذا نجح (أنت النسخة الوحيدة).
        يعيد False إذا فشل (هناك نسخة أخرى تعمل).
        """
        try:
            # محاولة الارتباط بالمنفذ على الـ localhost فقط
            self.sock.bind(('127.0.0.1', self.port))
            # نجح الارتباط، نبقي الـ socket مفتوحاً حتى نهاية البرنامج
            return True
        except socket.error as e:
            # المنفذ مشغول، يعني هناك نسخة أخرى تعمل
            logger.error(f"⛔ Socket Bind Failed: {e} - Port {self.port} is busy.")
            return False

    def __del__(self):
        try:
            self.sock.close()
        except Exception:
            pass

# ==========================================
# 📝 دوال التنسيق (Formatting)
# ==========================================

def sanitize_input(text: str, max_length: int = 4000) -> str:
    """
    تنظيف وتأمين المدخلات النصية.
    يقوم بإزالة أي وسوم HTML محتملة وتحديد طول النص.

    Args:
        text (str): النص المدخل.
        max_length (int): أقصى طول مسموح به.

    Returns:
        str: النص المنظف.
    """
    if not text:
        return ""
    # إزالة الأكواد الخبيثة المحتملة (بسيط)
    text = re.sub(r"<[^>]*>", "", text)
    return text[:max_length].strip()

def escape_markdown(text: str) -> str:
    """تنظيف النص من الأحرف الخاصة في Markdown لتجنب أخطاء parsing"""
    if not text:
        return ""
    # Characters that need escaping in Markdown
    for char in ['_', '*', '`', '[']:
        text = text.replace(char, '\\' + char)
    return text

def remove_tashkeel(text: str) -> str:
    """
    توحيد النص للبحث العربي:
    1. إزالة التشكيل.
    2. توحيد الهمزات (أ/إ/آ/ٱ -> ا، ؤ -> و، ئ -> ي، ء -> "").
    3. إزالة علامات الترقيم والرموز.
    4. توحيد المسافات.

    ملاحظة: أبقينا اسم الدالة كما هو للتوافق مع الاستدعاءات القديمة.
    """
    if not text:
        return ""


    text = str(text)

    # 1) إزالة التشكيل والعلامات المركبة العربية الشائعة
    tashkeel_pattern = re.compile(
        r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]"
    )
    text = tashkeel_pattern.sub("", text)

    # 2) توحيد الهمزات والألفات المتقاربة
    text = text.translate(str.maketrans({
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ؤ": "و",
        "ئ": "ي",
        "ء": "",
        "ـ": "",  # تطويل
    }))

    # 3) إزالة علامات الترقيم والرموز (Unicode P/S)
    # نستخدم regex لاستبدال كل ما ليس حرفاً أو رقماً أو مسافة بمسافة واحدة
    # [^\w\s] قد يحذف التشكيل إذا لم يتم حذفه مسبقاً، لكننا حذفناه في الخطوة 1
    # الـ regex التالي أكثر شمولاً لعلامات الترقيم والرموز
    text = re.sub(r'[^\w\s\u0621-\u064A\u0660-\u0669]', ' ', text, flags=re.UNICODE)

    # 4) توحيد المسافات
    text = re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()
    return text


def normalize_text(text: str) -> str:
    """
    تنظيف النص العربي وتوحيده للمقارنة:
    يعتمد على نفس منطق remove_tashkeel لضمان توحيد سلوك البحث
    في كل أماكن النظام.
    """
    return remove_tashkeel(text)


from core.validators import is_valid_url, contains_url


def split_long_message(text: str, max_length: int = 4000) -> list[str]:
    """
    تقسيم الرسائل الطويلة إلى أجزاء مناسبة لتليجرام.
    تحاول الدالة الحفاظ على وحدة الفقرات والجمل لضمان سهولة القراءة.

    Args:
        text (str): النص الكامل.
        max_length (int): الحد الأقصى لكل جزء.

    Returns:
        list[str]: قائمة بالأجزاء المقسمة.
    """
    if len(text) <= max_length:
        return [text]


    def _hard_split(chunk: str) -> list:
        return [chunk[i:i + max_length] for i in range(0, len(chunk), max_length)]

    def _split_words(chunk: str) -> list:
        words = chunk.split()
        if not words:
            return _hard_split(chunk)
        parts = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= max_length:
                current = candidate
            else:
                if current:
                    parts.append(current)
                    current = ""
                if len(word) <= max_length:
                    current = word
                else:
                    parts.extend(_hard_split(word))
                    current = ""
        if current:
            parts.append(current)
        return parts

    def _split_paragraph(paragraph: str) -> list:
        if len(paragraph) <= max_length:
            return [paragraph]
        # حاول التقسيم على الجمل أولاً
        sentences = re.split(r'(?<=[\.\!\?\u061F\u061B])\s+', paragraph)
        if len(sentences) <= 1:
            return _split_words(paragraph)
        parts = []
        current = ""
        for sentence in sentences:
            if not sentence:
                continue
            candidate = sentence if not current else f"{current} {sentence}"
            if len(candidate) <= max_length:
                current = candidate
            else:
                if current:
                    parts.append(current)
                    current = ""
                if len(sentence) <= max_length:
                    current = sentence
                else:
                    parts.extend(_split_words(sentence))
                    current = ""
        if current:
            parts.append(current)
        return parts

    # اختيار فاصل الفقرات
    para_sep = "\n\n" if "\n\n" in text else "\n"
    paragraphs = text.split(para_sep)

    parts = []
    current = ""
    for para in paragraphs:
        candidate = para if not current else f"{current}{para_sep}{para}"
        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            parts.append(current)
            current = ""

        if len(para) <= max_length:
            current = para
            continue

        para_parts = _split_paragraph(para)
        if para_parts:
            parts.extend(para_parts[:-1])
            current = para_parts[-1]

    if current:
        parts.append(current)

    return parts


def format_fatwa_card(fatwa: dict, use_markdown: bool = False) -> str:
    """تنسيق بطاقة الفتوى المختصرة"""
    status_icon = "🔓" if fatwa.get('status') == 'published' else "🔒 مسودة"

    def _esc(value: str) -> str:
        if not use_markdown:
            return value or ""
        return escape_markdown(str(value) if value else "")

    # تنسيق التصنيفات (قائمة مُسطَّحة بدون عناوين فقهية/موضوعية)
    items = []
    classifications = fatwa.get('classifications', [])
    if classifications:
        # Sort by slot_index (usually 1=Fiqh, 2=Topic)
        for cls in sorted(classifications, key=lambda x: x.get('slot_index', 0)):
             category_name = _esc(cls.get('category_name') or "")
             topic_names = [t for t in cls.get('topic_names', []) if t]
             topic_names = [_esc(t) for t in topic_names]

             if topic_names:
                 topic_str = " + ".join(topic_names)
                 items.append(f"{category_name} > {topic_str}")
             else:
                 items.append(category_name)

    if not items:
        items = ["غير مصنف"]
    cat_block = "\n".join([f"🏷️ {p}" for p in items])

    num = fatwa.get('fatwa_number', fatwa.get('id'))
    num_label = f"[{num}]" if not use_markdown else f"\\[{num}\\]"

    # فصل الرقم عن العنوان (بدون سطر التاريخ)
    title = _esc(fatwa.get('title') or '')
    scholar = _esc(fatwa.get('scholar_name') or 'غير محدد')
    
    if use_markdown:
        return (
            f"🔢 رقم الفتوى: {num_label}\n"
            f"📝 العنوان: **{title}**\n"
            f"👤 العالم: *{scholar}*\n"
            f"{cat_block}\n"
            f"👁️ المشاهدات: {fatwa.get('views', 0)}\n"
            f"{status_icon}"
        )
    else:
        return (
            f"🔢 رقم الفتوى: {num_label}\n"
            f"📝 العنوان: {title}\n"
            f"👤 العالم: {scholar}\n"
            f"{cat_block}\n"
            f"👁️ المشاهدات: {fatwa.get('views', 0)}\n"
            f"{status_icon}"
        )


def format_fatwa_content(fatwa: dict, use_markdown: bool = False) -> str:
    """تنسيق محتوى الفتوى الكامل - بدون روابط نصية"""
    fatwa_id_val = fatwa.get('fatwa_number', fatwa.get('id'))
    
    title = fatwa.get('title', '')
    scholar = fatwa.get('scholar_name', '')
    question = fatwa.get('question', '')
    answer = fatwa.get('answer', '')
    source_name = fatwa.get('source_name', '')
    source_title = fatwa.get('source_title', '')

    if use_markdown:
        title = escape_markdown(title)
        scholar = escape_markdown(scholar)
        question = escape_markdown(question)
        answer = escape_markdown(answer)
        source_name = escape_markdown(source_name)
        source_title = escape_markdown(source_title)

    if use_markdown:
        content = f"🔢 رقم الفتوى: {fatwa_id_val}\n"
        if scholar:
            content += f"👤 المفتي: *{scholar}*\n"
        content += f"📝 العنوان: **{title}**\n"
        content += f"──────────────\n\n"
        if question:
            content += f"❓ السؤال:\n{question}\n\n"
        content += f"💡 الجواب:\n{answer}\n\n"
        content += f"──────────────\n"
    else:
        content = f"🔢 رقم الفتوى: {fatwa_id_val}\n"
        if scholar:
            content += f"👤 المفتي: {scholar}\n"
        content += f"📝 العنوان: {title}\n"
        content += f"──────────────\n\n"
        if question:
            content += f"❓ السؤال:\n{question}\n\n"
        content += f"💡 الجواب:\n{answer}\n\n"
        content += f"──────────────\n\n"

    if source_name:
        source_line = f"📚 المصدر: {source_name}"
        if source_title:
            source_line += f" — {source_title}"
        content += source_line + "\n"

    content += "\n"

    # التصنيفات والمواضيع (عرض مُسطَّح بدون عناوين الفقهي/الموضوعي)
    # التصنيفات والمواضيع
    classifications = fatwa.get('classifications', [])
    if classifications:
        items = []
        for cls in sorted(classifications, key=lambda x: x.get('slot_index', 0)):
             category_name = cls.get('category_name')
             topic_names = cls.get('topic_names', [])

             if topic_names:
                 topic_str = ", ".join(topic_names)
                 items.append(f"{category_name} > {topic_str}")
             else:
                 items.append(category_name)

        if items:
            content += "\n".join([f"🏷️ {p}" for p in items]) + "\n"

    return content

def build_fatwa_preview_text(fatwa: dict, max_length: int = 3600) -> tuple[str, bool]:
    """Build a preview text that never exceeds max_length."""
    num = fatwa.get('fatwa_number', fatwa.get('id'))
    title = fatwa.get('title', '')
    scholar = fatwa.get('scholar_name') or "غير محدد"
    question = (fatwa.get('question') or "").strip()
    answer = (fatwa.get('answer') or "").strip()

    header = (
        f"🔢 رقم الفتوى: {num}\n"
        f"👤 المفتي: {scholar}\n"
        f"📝 العنوان: {title}\n"
        "──────────────\n\n"
    )

    question_block = ""
    if question:
        question_block = f"❓ السؤال:\n{question}\n\n"

    answer_label = "💡 الجواب:\n"

    footer_lines = []
    if fatwa.get('source_name'):
        source_line = f"📚 المصدر: {fatwa['source_name']}"
        if fatwa.get('source_title'):
            if str(fatwa.get('source_title')).strip():
                source_line += f" — {fatwa['source_title']}"
        footer_lines.append(source_line)

    # Categories / topics lines
    classifications = fatwa.get('classifications', [])
    if classifications:
        for cls in sorted(classifications, key=lambda x: x.get('slot_index', 0)):
             category_name = cls.get('category_name')
             topic_names = cls.get('topic_names', [])

             if topic_names:
                 topic_str = ", ".join(topic_names)
                 footer_lines.append(f"🏷️ {category_name} > {topic_str}")
             else:
                 footer_lines.append(f"🏷️ {category_name}")

    footer = ("\n\n" + "\n".join(footer_lines)) if footer_lines else ""

    full_text = header + question_block + answer_label + answer + footer
    if len(full_text) <= max_length:
        return full_text, False

    tail = "\n\n... يتبع\n──────────────\n⬇️ لقراءة باقي الفتوى اضغط زر تابع القراءة"

    base = header + question_block + answer_label
    allowed = max_length - len(base) - len(tail) - len(footer)

    if allowed < 0 and question:
        q_prefix = "❓ السؤال:\n"
        q_suffix = "\n\n"
        max_q_len = max_length - len(header) - len(answer_label) - len(tail) - len(footer) - len(q_prefix) - len(q_suffix)
        if max_q_len < 0:
            max_q_len = 0
        question = question[:max_q_len].rstrip()
        question_block = f"{q_prefix}{question}{q_suffix}" if question else ""
        base = header + question_block + answer_label
        allowed = max_length - len(base) - len(tail) - len(footer)

    if allowed < 0:
        allowed = 0

    answer_snippet = answer[:allowed].rstrip()
    text = base + answer_snippet + tail + footer
    return text, True

def format_full_fatwa_for_copy(fatwa: dict) -> str:
    """تنسيق الفتوى للنسخ الكامل مع تضمين الروابط داخل النص."""
    signature = "🤖 بوت إدارة الفتاوى | t.me/Fatwa_CMS_Bot"
    text = format_fatwa_content(fatwa, use_markdown=False)
    link_lines = []
    if fatwa.get('source_url'):
        link_lines.append(f"📚 رابط المصدر:\n{str(fatwa['source_url']).strip()}")
    if fatwa.get('audio_url'):
        link_lines.append(f"🎧 رابط الصوتية:\n{str(fatwa['audio_url']).strip()}")
    if link_lines:
        text = text.rstrip() + "\n\n" + "\n".join(link_lines)
    if signature in text:
        return text
    return f"{text}\n\n{signature}"

# ==========================================
# ⌨️ دوال لوحات المفاتيح (Keyboards)
# ==========================================
# (يمكن وضعها هنا أو في ملفات Handlers، سنضع بعض الأساسيات هنا)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.keyboards import create_main_keyboard, back_to_main_keyboard

def back_to_search_keyboard(label: str = "🔙 رجوع للبحث") -> InlineKeyboardMarkup:
    """
    لوحة قياسية للرجوع إلى شاشة البحث (search_fatwas)
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="search_fatwas")]])


def back_to_categories_keyboard(label: str = "🔙 إدارة التصنيفات") -> InlineKeyboardMarkup:
    """
    لوحة قياسية للرجوع إلى إدارة التصنيفات (manage_categories)
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="manage_categories")]])


# ==========================================
# 🛡️ وظائف الإرسال الآمن (Retry Logic)
# ==========================================

async def safe_reply_text(message_obj, text, **kwargs):
    """
    إرسال رد بطريقة آمنة مع محاولة الإعادة في حال حدوث خطأ في الشبكة.

    Args:
        message_obj: كائن الرسالة (update.message)
        text: النص المراد إرساله
        max_retries: عدد محاولات الإعادة (الافتراضي 3)
        retry_delay: التأخير بين المحاولات بالثواني (الافتراضي 2)
    """
    from telegram.error import NetworkError, TimedOut
    import asyncio

    max_retries = kwargs.pop('max_retries', 3)
    retry_delay = kwargs.pop('retry_delay', 2)

    for attempt in range(max_retries):
        try:
            return await message_obj.reply_text(text, **kwargs)
        except (NetworkError, TimedOut) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to reply after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Network error while replying (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.error(f"Unexpected error in safe_reply_text: {e}")
            raise

async def safe_send_message(bot, chat_id, text, **kwargs):
    """
    إرسال رسالة بطريقة آمنة مع محاولة الإعادة.

    Args:
        bot: كائن البوت (context.bot)
        chat_id: معرف الدردشة
        text: النص المراد إرساله
    """
    from telegram.error import NetworkError, TimedOut
    import asyncio

    max_retries = kwargs.pop('max_retries', 3)
    retry_delay = kwargs.pop('retry_delay', 2)

    for attempt in range(max_retries):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except (NetworkError, TimedOut) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to send message to {chat_id} after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Network error for {chat_id} (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            logger.error(f"Unexpected error in safe_send_message: {e}")
            raise

async def notify_new_subscription(bot, entity_type: str, entity_data: dict, context=None):
    """
    إرسال إشعار للمالك عند اشتراك مستخدم جديد أو إضافة قناة/مجموعة.
    
    Args:
        bot: كائن البوت
        entity_type: نوع الكيان ('user', 'channel', 'group')
        entity_data: بيانات الكيان (id, name, username, etc.)
        context: سياق التحديث (اختياري)
    """
    from core.config import OWNER_ID
    
    if not OWNER_ID:
        return

    try:
        title = "🔔 إشعار اشتراك جديد"
        
        if entity_type == 'user':
            icon = "👤"
            type_label = "مستخدم جديد"
        elif entity_type == 'channel':
            icon = "📢"
            type_label = "قناة جديدة"
        elif entity_type == 'group':
            icon = "👥"
            type_label = "مجموعة جديدة"
        else:
            icon = "ℹ️"
            type_label = entity_type

        name = escape_markdown(entity_data.get('name') or "بدون اسم")
        username = escape_markdown(entity_data.get('username') or "بدون معرف")
        if username and username != "بدون معرف":
            username = f"@{username}"
            
        entity_id = entity_data.get('id')
        
        text = (
            f"{title}\n\n"
            f"{icon} **النوع:** {type_label}\n"
            f"📝 **الاسم:** {name}\n"
            f"🆔 **الآيدي:** `{entity_id}`\n"
            f"🔗 **المعرف:** {username}\n"
            f"📅 **التاريخ:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await safe_send_message(bot, OWNER_ID, text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Failed to send subscription notification: {e}")

async def safe_edit_message_text(query_obj, text, **kwargs):
    """
    تعديل رسالة بطريقة آمنة مع محاولة الإعادة.

    Args:
        query_obj: كائن الاستعلام (update.callback_query) أو الرسالة المراد تعديلها
        text: النص الجديد
    """
    from telegram.error import NetworkError, TimedOut
    import asyncio

    max_retries = kwargs.pop('max_retries', 3)
    retry_delay = kwargs.pop('retry_delay', 2)

    for attempt in range(max_retries):
        try:
            return await query_obj.edit_message_text(text, **kwargs)
        except (NetworkError, TimedOut) as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to edit message after {max_retries} attempts: {e}")
                raise
            logger.warning(f"Network error while editing (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {retry_delay}s...")
            await asyncio.sleep(retry_delay)
        except Exception as e:
            # ignore "Message is not modified" error which is common in edit calls
            if "Message is not modified" in str(e):
                return
            logger.error(f"Unexpected error in safe_edit_message_text: {e}")
            raise
