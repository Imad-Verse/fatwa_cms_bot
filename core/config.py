"""
إعدادات البوت (core/config.py)
---------------------------
تحتوي على الثوابت، الإعدادات، ومعرفات الحالات (States).
يتم تحميل القيم الحساسة من ملف .env.
"""

import os
from dotenv import load_dotenv

# تحميل المتغيرات من .env
load_dotenv()

# --- إعدادات أساسية ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is required")

# قائمة المعرفات (الأدمن)
OWNER_ID = int(os.getenv("OWNER_ID", 0))
ADMIN_IDS = [int(i.strip()) for i in os.getenv("ADMIN_IDS", "").split(",") if i.strip()]
if OWNER_ID and OWNER_ID not in ADMIN_IDS:
    ADMIN_IDS.append(OWNER_ID)

# القناة الرسمية للنشر
CHANNEL_ID = os.getenv("CHANNEL_ID")

# --- إعدادات الشبكة ---
PROXY_URL = os.getenv("PROXY_URL")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30))

# --- إعدادات Groq (للبحث الذكي) ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama3-70b-8192")

# --- المسارات وقواعد البيانات ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
FATWAS_DB_NAME = "fatwa_bot.db"
BOT_DB_NAME = "bot_internal.db"

DB_PATH = os.path.join(DATA_DIR, FATWAS_DB_NAME)
BOT_DB_PATH = os.path.join(DATA_DIR, BOT_DB_NAME)
LOGS_DIR = os.path.join(BASE_DIR, "logs")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
TEMP_DIR = os.path.join(BASE_DIR, "temp")

# إنشاء المجلدات إذا لم تكن موجودة
for d in [DATA_DIR, LOGS_DIR, BACKUP_DIR, TEMP_DIR]:
    if not os.path.exists(d):
        os.makedirs(d)

# --- إعدادات القفل ---
LOCK_PORT = int(os.getenv("LOCK_PORT", 12345))

# ==========================================
# 🚦 تعريف الحالات (States - ConversationHandler)
# ==========================================

_STATES_LIST = [
    # حالات إضافة فتوى جديدة
    "STATE_TITLE",
    "STATE_SCHOLAR",
    "STATE_QUESTION",
    "STATE_FATWA_TEXT",
    "STATE_CATEGORIES",
    "STATE_TOPICS",
    "STATE_TAXONOMY_MENU",
    "STATE_CATEGORY_1",
    "STATE_CATEGORY_2",
    "STATE_SOURCE",
    "STATE_SOURCE_TITLE",
    "STATE_SOURCE_URL",
    "STATE_AUDIO",

    # حالات البحث
    "STATE_SEARCH",
    "STATE_SEARCH_TITLE",
    "STATE_SEARCH_ALL",
    "STATE_SEARCH_SCHOLAR",
    "STATE_SEARCH_SCHOLAR_QUERY",
    "STATE_SEARCH_CATEGORY",
    "STATE_SEARCH_TOPIC",
    "STATE_SEARCH_SOURCE",
    "STATE_SEARCH_SOURCE_QUERY",
    "STATE_SEARCH_NUMBER",
    "STATE_SEARCH_AI",
    "STATE_SEARCH_SMART",
    "STATE_SMART_SEARCH_SCHOLARS",
    "STATE_SMART_SEARCH_QUERY",

    # حالات البحث المتقدم أثناء الإضافة
    "STATE_ADD_FATWA_SCHOLAR_SEARCH",
    "STATE_ADD_FATWA_CAT_SEARCH",
    "STATE_ADD_FATWA_TOPIC_SEARCH",

    "STATE_SEARCH_CAT_SEARCH",
    "STATE_SEARCH_TOPIC_SEARCH",

    # حالات التعديل
    "STATE_EDIT_MENU",
    "STATE_EDIT_VALUE",
    "STATE_EDIT_CATEGORY",
    "STATE_EDIT_TOPIC",

    # حالات البحث/الإضافة أثناء التعديل
    "STATE_EDIT_CAT_SEARCH",
    "STATE_EDIT_NEW_CAT",

    # حالات تحديث الموضوع (جديد)
    "STATE_EDIT_TOP_SEARCH",
    "STATE_EDIT_NEW_TOP",

    # حالات الإدارة
    "STATE_ADMIN_ADD",
    "STATE_ADMIN_REMOVE",

    # حالات النسخ الاحتياطي
    "STATE_BACKUP_CONFIRM",

    # حالات التصنيفات
    "STATE_CATEGORY_ADD",
    "STATE_CATEGORY_REMOVE",

    # حالات المواضيع
    "STATE_TOPIC_ADD",
    "STATE_TOPIC_EDIT",

    # حالات إضافية للإدارة
    "STATE_ADMIN_SEARCH_CAT",
    "STATE_CATEGORY_EDIT",
    "STATE_SOURCE_ADD",
    "STATE_SOURCE_EDIT",

    # حالات إدارة العلماء
    "STATE_SCHOLAR_ADD",
    "STATE_SCHOLAR_BIO",
    "STATE_SCHOLAR_BIO_CONFIRM",
    "STATE_SCHOLAR_WEBSITE",

    # حالات البودكاست
    "STATE_PODCAST_CONTENT",
    "STATE_PODCAST_EDIT",

    # حالات إعدادات الجدولة
    "STATE_SETTINGS_DAILY_TIME",
    "STATE_SETTINGS_WEEKLY_TIME",
]

# تعيين الحالات كمتغيرات عالمية لتسهيل الوصول إليها
(
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
    STATE_SEARCH_SMART,
    STATE_SMART_SEARCH_SCHOLARS,
    STATE_SMART_SEARCH_QUERY,

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
    STATE_PODCAST_EDIT,

    # حالات إعدادات الجدولة
    STATE_SETTINGS_DAILY_TIME,
    STATE_SETTINGS_WEEKLY_TIME,
) = range(len(_STATES_LIST))
