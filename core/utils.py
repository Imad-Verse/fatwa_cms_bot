"""
وحدة الأدوات والخدمات العامة (core/utils.py)
---------------------------------------
تحتوي على الدوال المساعدة والأنظمة الفرعية للبوت:
1. الأنظمة: التخزين المؤقت، مراقبة الأداء، وتحديد معدل الطلبات.
2. التنسيق: تنظيف النصوص وتجهيز رسائل الماركدوان (Markdown).
3. العمليات: إرسال وتحرير الرسائل بشكل آمن مع معالجة الأخطاء.
"""

import os
import sys
import socket
import asyncio
import time
import threading
import logging
from collections import defaultdict
from datetime import datetime
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

    def clear(self):
        with self.lock:
            self.cache.clear()

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
        self.lock = threading.Lock()

    def is_fast_repeat(self, user_id: int, data: str) -> bool:
        """
        يعيد True إذا كان الضغط مكرراً خلال فترة أقل من min_interval
        وإلا يقوم بتسجيل الوقت الحالي ويعيد False.
        """
        now = time.time()
        with self.lock:
            user_map = self.last_pressed[user_id]
            last_time = user_map.get(data, 0)
            if now - last_time < self.min_interval:
                return True
            user_map[data] = now
            return False

# إنشاء نسخ من الأنظمة
cache = CacheManager()
monitor = BotMonitor()
rate_limiter = RateLimiter(max_requests=15, period=60)
callback_guard = CallbackGuard(min_interval=1.5)

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
    """تنظيف وتأمين المدخلات"""
    if not text:
        return ""
    # إزالة الأكواد الخبيثة المحتملة (بسيط)
    text = text.replace('<script>', '').replace('</script>', '')
    return text[:max_length].strip()

def escape_markdown(text: str) -> str:
    """تنظيف النص من الأحرف الخاصة في Markdown لتجنب أخطاء parsing

    Args:
        text: النص المراد تنظيفه

    Returns:
        النص بعد escape الأحرف الخاصة
    """
    if not text:
        return ""

    # الأحرف الخاصة في Markdown v1
    special_chars = ['_', '*', '`', '[']

    for char in special_chars:
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

    import re
    import unicodedata

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

    # 3) إزالة علامات الترقيم والرموز (Unicode P/S) بتحويلها لمسافة
    chars = []
    for ch in text:
        category = unicodedata.category(ch)
        if category.startswith("P") or category.startswith("S"):
            chars.append(" ")
        else:
            chars.append(ch)
    text = "".join(chars)

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


def is_valid_url(text: str) -> bool:
    """Check if the text is a valid URL."""
    import re
    # Simplified regex for practical use
    regex = re.compile(
        r'^(?:http|ftp)s?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' # domain...
        r'localhost|' # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, text) is not None

def contains_url(text: str) -> bool:
    """Check if the text contains any URL."""
    import re
    regex = re.compile(
        r'(?:http|ftp)s?://' # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' # domain...
        r'localhost|' # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
        r'(?::\d+)?' # optional port
        r'(?:/?|[/?]\S+)', re.IGNORECASE)
    return re.search(regex, text) is not None


def split_long_message(text: str, max_length: int = 4000) -> list:
    """تقسيم الرسائل الطويلة إلى أجزاء مناسبة لتليجرام، مع تفضيل حدود الفقرات."""
    if len(text) <= max_length:
        return [text]

    import re

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
        return escape_markdown(value or "")

    # تنسيق التصنيفات (قائمة مُسطَّحة بدون عناوين فقهية/موضوعية)
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
    return (
        f"🔢 رقم الفتوى: {num_label}\n"
        f"📝 العنوان: {_esc(fatwa.get('title') or '')}\n"
        f"👤 العالم: {_esc(fatwa.get('scholar_name') or 'غير محدد')}\n"
        f"{cat_block}\n"
        f"👁️ المشاهدات: {fatwa.get('views', 0)}\n"
        f"{status_icon}"
    )


def format_fatwa_content(fatwa: dict, use_markdown: bool = False) -> str:
    """تنسيق محتوى الفتوى الكامل - بدون روابط نصية

    Args:
        fatwa: بيانات الفتوى
        use_markdown: استخدام تنسيق Markdown (افتراضي: False لتجنب أخطاء parsing)
    """
    fatwa_id_val = fatwa.get('fatwa_number', fatwa.get('id'))

    if use_markdown:
        content = f"🔢 رقم الفتوى: {fatwa_id_val}\n"
        if fatwa.get('scholar_name'):
            content += f"👤 المفتي: {fatwa['scholar_name']}\n"
        content += f"📝 العنوان: {fatwa['title']}\n"

        content += f"──────────────\n\n"

        if fatwa.get('question'):
            content += f"❓ السؤال:\n{fatwa['question']}\n\n"

        content += f"💡 الجواب:\n{fatwa['answer']}\n\n"
        content += f"──────────────\n"
    else:
        # نسخة بدون markdown لتجنب أخطاء parsing
        content = f"🔢 رقم الفتوى: {fatwa_id_val}\n"
        if fatwa.get('scholar_name'):
            content += f"👤 المفتي: {fatwa['scholar_name']}\n"
        content += f"📝 العنوان: {fatwa['title']}\n"

        content += f"──────────────\n\n"

        if fatwa.get('question'):
            content += f"❓ السؤال:\n{fatwa['question']}\n\n"

        content += f"💡 الجواب:\n{fatwa['answer']}\n\n"
        content += f"──────────────\n\n"

    # المصدر (فقط بدون روابط نصية) - تم تقديمه قبل التصنيف
    if fatwa.get('source_name'):
        source_line = f"📚 المصدر: {fatwa['source_name']}"
        if fatwa.get('source_title'):
            source_line += f" — {fatwa['source_title']}"
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

def create_main_keyboard(is_admin=False):
    """إنشاء لوحة المفاتيح الرئيسية"""
    keyboard = [
        [InlineKeyboardButton("🔍 بحث عن فتوى", callback_data="search_fatwas"), InlineKeyboardButton("📖 مطالعة الفتاوى", callback_data="browse_fatwas")],
        [InlineKeyboardButton("🔥 الأكثر مشاهدة", callback_data="search_popular"), InlineKeyboardButton("📅 أحدث الفتاوى", callback_data="search_latest")],
        [InlineKeyboardButton("⭐ مفضلتك", callback_data="my_favorites"), InlineKeyboardButton("🌟 المفضلة", callback_data="top_favorites")],
        [InlineKeyboardButton("➕ أضفه إلى قناتك أو مجموعتك", callback_data="how_to_add_bot"), InlineKeyboardButton("📨 ارسل فتوى لقناتك", callback_data="user_send_fatwa")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"), InlineKeyboardButton("ℹ️ حول البوت", callback_data="help_info")],
    ]

    if is_admin:
        # إضافة زر إضافة فتوى بجانب البحث للأدمن -> REMOVED per request
        # keyboard[0].append(InlineKeyboardButton("➕ إضافة فتوى", callback_data="add_fatwa"))
        pass


    if is_admin:
        # فقط زر لوحة الإدارة
        keyboard.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])

    # أزرار معلومات (Removed, merged above)
    # info_row = [ ... ]
    # keyboard.append(info_row)

    return InlineKeyboardMarkup(keyboard)


# ==========================================
# 🔙 دوال مساعدة للرجوع (Back Keyboards)
# ==========================================

def back_to_main_keyboard(label: str = "\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629") -> InlineKeyboardMarkup:
    """
    لوحة قياسية للرجوع إلى القائمة الرئيسية (back_main)

    لا تغيّر منطق العمل: ما زال callback_data = "back_main"
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="back_main")]])


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
