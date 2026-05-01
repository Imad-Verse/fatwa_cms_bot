"""
ملف إعدادات البوت (config.py)
-----------------------------
يحتوي هذا الملف على:
1. الثوابت العامة (مسارات، توكن).
2. تعريف حالات المحادثة (States).
3. إعدادات أخرى.
"""

import os
from dotenv import load_dotenv

# تحميل متغيرات البيئة
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

# ==========================================
# الإعدادات العامة
# ==========================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is required")

OWNER_ID = int(os.getenv("OWNER_ID", 362464035))

# المسارات
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# قاعدة بيانات الفتاوى (منفصلة بالكامل)
FATWAS_DB_NAME = os.path.join(BASE_DIR, "data", "fatwas.db")
# قاعدة بيانات التشغيل (المستخدمين/القنوات/الإعدادات)
BOT_DB_NAME = os.path.join(BASE_DIR, "data", "bot_users.db")
# للإبقاء على توافق داخلي قديم إن وُجد
DB_NAME = FATWAS_DB_NAME
BACKUP_DIR = os.path.join(BASE_DIR, "data", "backups")
TEMP_DIR = os.path.join(BASE_DIR, "temp")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOCK_PORT = 12345 # منفذ القفل لمنع تكرار التشغيل

# إعدادات أخرى
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 60))
PROXY_URL = os.getenv("PROXY_URL") # مثال: http://127.0.0.1:10809 أو socks5://127.0.0.1:10808
GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
GROQ_MODEL = (os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile").strip()

# تأكد من وجود المجلدات
for directory in [BACKUP_DIR, TEMP_DIR, LOGS_DIR]:
    os.makedirs(directory, exist_ok=True)


# ==========================================
# 🚦 تعريف الحالات (States - ConversationHandler)
# ==========================================

(
    # حالات إضافة فتوى جديدة
    STATE_TITLE,
    STATE_SCHOLAR,
    STATE_QUESTION,
    STATE_FATWA_TEXT,
    STATE_CATEGORIES,
    STATE_TOPICS,
    STATE_TAXONOMY_MENU,
    STATE_CATEGORY_1,
    STATE_CATEGORY_2,
    STATE_SOURCE,
    STATE_SOURCE_TITLE,
    STATE_SOURCE_URL,
    STATE_AUDIO,

    # حالات البحث
    STATE_SEARCH,
    STATE_SEARCH_TITLE,
    STATE_SEARCH_ALL,
    STATE_SEARCH_SCHOLAR,
    STATE_SEARCH_SCHOLAR_QUERY,
    STATE_SEARCH_CATEGORY,
    STATE_SEARCH_TOPIC,
    STATE_SEARCH_SOURCE,
    STATE_SEARCH_SOURCE_QUERY,
    STATE_SEARCH_NUMBER,
    STATE_SEARCH_AI,

    # حالات البحث المتقدم أثناء الإضافة
    STATE_ADD_FATWA_SCHOLAR_SEARCH,
    STATE_ADD_FATWA_CAT_SEARCH,
    STATE_ADD_FATWA_TOPIC_SEARCH,

    STATE_SEARCH_CAT_SEARCH,
    STATE_SEARCH_TOPIC_SEARCH,

    # حالات التعديل
    STATE_EDIT_MENU,
    STATE_EDIT_VALUE,
    STATE_EDIT_CATEGORY,
    STATE_EDIT_TOPIC,

    # حالات البحث/الإضافة أثناء التعديل
    STATE_EDIT_CAT_SEARCH,
    STATE_EDIT_NEW_CAT,

    # حالات تحديث الموضوع (جديد)
    STATE_EDIT_TOP_SEARCH,
    STATE_EDIT_NEW_TOP,

    # حالات الإدارة
    STATE_ADMIN_ADD,
    STATE_ADMIN_REMOVE,

    # حالات النسخ الاحتياطي
    STATE_BACKUP_CONFIRM,

    # حالات التصنيفات
    STATE_CATEGORY_ADD,
    STATE_CATEGORY_REMOVE,

    # حالات المواضيع
    STATE_TOPIC_ADD,
    STATE_TOPIC_EDIT,

    # حالات إضافية للإدارة
    STATE_ADMIN_SEARCH_CAT,
    STATE_CATEGORY_EDIT,
    STATE_SOURCE_ADD,
    STATE_SOURCE_EDIT,
    # حالات إدارة العلماء
    STATE_SCHOLAR_ADD,
    STATE_SCHOLAR_BIO,
    STATE_SCHOLAR_BIO_CONFIRM,
    STATE_SCHOLAR_WEBSITE,

    # حالات البودكاست
    STATE_PODCAST_CONTENT,

    # حالات إعدادات الجدولة
    STATE_SETTINGS_DAILY_TIME,
    STATE_SETTINGS_WEEKLY_TIME,
    STATE_SMART_SEARCH,
    STATE_SMART_SEARCH_SCHOLARS,
    STATE_SMART_SEARCH_QUERY,
) = range(58)
