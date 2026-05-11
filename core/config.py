import os
from dotenv import load_dotenv
from enum import IntEnum, auto

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
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
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

class BotState(IntEnum):
    # حالات إضافة فتوى جديدة
    STATE_TITLE = 0
    STATE_SCHOLAR = auto()
    STATE_QUESTION = auto()
    STATE_FATWA_TEXT = auto()
    STATE_CATEGORIES = auto()
    STATE_TOPICS = auto()
    STATE_TAXONOMY_MENU = auto()
    STATE_CATEGORY_1 = auto()
    STATE_CATEGORY_2 = auto()
    STATE_SOURCE = auto()
    STATE_SOURCE_TITLE = auto()
    STATE_SOURCE_URL = auto()
    STATE_AUDIO = auto()

    # حالات البحث
    STATE_SEARCH = auto()
    STATE_SEARCH_TITLE = auto()
    STATE_SEARCH_ALL = auto()
    STATE_SEARCH_SCHOLAR = auto()
    STATE_SEARCH_SCHOLAR_QUERY = auto()
    STATE_SEARCH_CATEGORY = auto()
    STATE_SEARCH_TOPIC = auto()
    STATE_SEARCH_SOURCE = auto()
    STATE_SEARCH_SOURCE_QUERY = auto()
    STATE_SEARCH_NUMBER = auto()
    STATE_SEARCH_AI = auto()
    STATE_SEARCH_SMART = auto()
    STATE_SMART_SEARCH_SCHOLARS = auto()
    STATE_SMART_SEARCH_QUERY = auto()

    # حالات البحث المتقدم أثناء الإضافة
    STATE_ADD_FATWA_SCHOLAR_SEARCH = auto()
    STATE_ADD_FATWA_CAT_SEARCH = auto()
    STATE_ADD_FATWA_TOPIC_SEARCH = auto()

    STATE_SEARCH_CAT_SEARCH = auto()
    STATE_SEARCH_TOPIC_SEARCH = auto()

    # حالات التعديل
    STATE_EDIT_MENU = auto()
    STATE_EDIT_VALUE = auto()
    STATE_EDIT_CATEGORY = auto()
    STATE_EDIT_TOPIC = auto()

    # حالات البحث/الإضافة أثناء التعديل
    STATE_EDIT_CAT_SEARCH = auto()
    STATE_EDIT_NEW_CAT = auto()

    # حالات تحديث الموضوع (جديد)
    STATE_EDIT_TOP_SEARCH = auto()
    STATE_EDIT_NEW_TOP = auto()

    # حالات الإدارة
    STATE_ADMIN_ADD = auto()
    STATE_ADMIN_REMOVE = auto()

    # حالات النسخ الاحتياطي
    STATE_BACKUP_CONFIRM = auto()

    # حالات التصنيفات
    STATE_CATEGORY_ADD = auto()
    STATE_CATEGORY_REMOVE = auto()

    # حالات المواضيع
    STATE_TOPIC_ADD = auto()
    STATE_TOPIC_EDIT = auto()

    # حالات إضافية للإدارة
    STATE_ADMIN_SEARCH_CAT = auto()
    STATE_CATEGORY_EDIT = auto()
    STATE_SOURCE_ADD = auto()
    STATE_SOURCE_EDIT = auto()

    # حالات إدارة العلماء
    STATE_SCHOLAR_ADD = auto()
    STATE_SCHOLAR_BIO = auto()
    STATE_SCHOLAR_BIO_CONFIRM = auto()
    STATE_SCHOLAR_WEBSITE = auto()

    # حالات البودكاست
    STATE_PODCAST_CONTENT = auto()
    STATE_PODCAST_EDIT = auto()

    # حالات إعدادات الجدولة
    STATE_SETTINGS_DAILY_TIME = auto()
    STATE_SETTINGS_WEEKLY_TIME = auto()

# تصدير الحالات كمتغيرات عالمية للتوافق مع الكود القديم
STATE_TITLE = BotState.STATE_TITLE
STATE_SCHOLAR = BotState.STATE_SCHOLAR
STATE_QUESTION = BotState.STATE_QUESTION
STATE_FATWA_TEXT = BotState.STATE_FATWA_TEXT
STATE_CATEGORIES = BotState.STATE_CATEGORIES
STATE_TOPICS = BotState.STATE_TOPICS
STATE_TAXONOMY_MENU = BotState.STATE_TAXONOMY_MENU
STATE_CATEGORY_1 = BotState.STATE_CATEGORY_1
STATE_CATEGORY_2 = BotState.STATE_CATEGORY_2
STATE_SOURCE = BotState.STATE_SOURCE
STATE_SOURCE_TITLE = BotState.STATE_SOURCE_TITLE
STATE_SOURCE_URL = BotState.STATE_SOURCE_URL
STATE_AUDIO = BotState.STATE_AUDIO

STATE_SEARCH = BotState.STATE_SEARCH
STATE_SEARCH_TITLE = BotState.STATE_SEARCH_TITLE
STATE_SEARCH_ALL = BotState.STATE_SEARCH_ALL
STATE_SEARCH_SCHOLAR = BotState.STATE_SEARCH_SCHOLAR
STATE_SEARCH_SCHOLAR_QUERY = BotState.STATE_SEARCH_SCHOLAR_QUERY
STATE_SEARCH_CATEGORY = BotState.STATE_SEARCH_CATEGORY
STATE_SEARCH_TOPIC = BotState.STATE_SEARCH_TOPIC
STATE_SEARCH_SOURCE = BotState.STATE_SEARCH_SOURCE
STATE_SEARCH_SOURCE_QUERY = BotState.STATE_SEARCH_SOURCE_QUERY
STATE_SEARCH_NUMBER = BotState.STATE_SEARCH_NUMBER
STATE_SEARCH_AI = BotState.STATE_SEARCH_AI
STATE_SEARCH_SMART = BotState.STATE_SEARCH_SMART
STATE_SMART_SEARCH_SCHOLARS = BotState.STATE_SMART_SEARCH_SCHOLARS
STATE_SMART_SEARCH_QUERY = BotState.STATE_SMART_SEARCH_QUERY

STATE_ADD_FATWA_SCHOLAR_SEARCH = BotState.STATE_ADD_FATWA_SCHOLAR_SEARCH
STATE_ADD_FATWA_CAT_SEARCH = BotState.STATE_ADD_FATWA_CAT_SEARCH
STATE_ADD_FATWA_TOPIC_SEARCH = BotState.STATE_ADD_FATWA_TOPIC_SEARCH

STATE_SEARCH_CAT_SEARCH = BotState.STATE_SEARCH_CAT_SEARCH
STATE_SEARCH_TOPIC_SEARCH = BotState.STATE_SEARCH_TOPIC_SEARCH

STATE_EDIT_MENU = BotState.STATE_EDIT_MENU
STATE_EDIT_VALUE = BotState.STATE_EDIT_VALUE
STATE_EDIT_CATEGORY = BotState.STATE_EDIT_CATEGORY
STATE_EDIT_TOPIC = BotState.STATE_EDIT_TOPIC

STATE_EDIT_CAT_SEARCH = BotState.STATE_EDIT_CAT_SEARCH
STATE_EDIT_NEW_CAT = BotState.STATE_EDIT_NEW_CAT

STATE_EDIT_TOP_SEARCH = BotState.STATE_EDIT_TOP_SEARCH
STATE_EDIT_NEW_TOP = BotState.STATE_EDIT_NEW_TOP

STATE_ADMIN_ADD = BotState.STATE_ADMIN_ADD
STATE_ADMIN_REMOVE = BotState.STATE_ADMIN_REMOVE

STATE_BACKUP_CONFIRM = BotState.STATE_BACKUP_CONFIRM

STATE_CATEGORY_ADD = BotState.STATE_CATEGORY_ADD
STATE_CATEGORY_REMOVE = BotState.STATE_CATEGORY_REMOVE

STATE_TOPIC_ADD = BotState.STATE_TOPIC_ADD
STATE_TOPIC_EDIT = BotState.STATE_TOPIC_EDIT

STATE_ADMIN_SEARCH_CAT = BotState.STATE_ADMIN_SEARCH_CAT
STATE_CATEGORY_EDIT = BotState.STATE_CATEGORY_EDIT
STATE_SOURCE_ADD = BotState.STATE_SOURCE_ADD
STATE_SOURCE_EDIT = BotState.STATE_SOURCE_EDIT

STATE_SCHOLAR_ADD = BotState.STATE_SCHOLAR_ADD
STATE_SCHOLAR_BIO = BotState.STATE_SCHOLAR_BIO
STATE_SCHOLAR_BIO_CONFIRM = BotState.STATE_SCHOLAR_BIO_CONFIRM
STATE_SCHOLAR_WEBSITE = BotState.STATE_SCHOLAR_WEBSITE

STATE_PODCAST_CONTENT = BotState.STATE_PODCAST_CONTENT
STATE_PODCAST_EDIT = BotState.STATE_PODCAST_EDIT

STATE_SETTINGS_DAILY_TIME = BotState.STATE_SETTINGS_DAILY_TIME
STATE_SETTINGS_WEEKLY_TIME = BotState.STATE_SETTINGS_WEEKLY_TIME
