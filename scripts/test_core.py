import asyncio
import sys
import os

# إضافة مجلد المشروع للمسار لضمان عمل الاستيرادات
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import normalize_text, cache
from core.bot_db import BotDatabaseManager
from core.database import FatwaDatabaseManager

async def run_tests():
    print("--- Starting Core Component Tests ---")
    
    # 1. Test Text Normalization
    print("\n[1/3] Testing Text Normalization...")
    original = "أهلاً بكَ يَا مُحمّدُ!"
    normalized = normalize_text(original)
    expected = "اهلا بك يا محمد"
    if normalized == expected:
        print("OK: Normalization SUCCESS")
    else:
        print(f"FAIL: Normalization FAILED (Got: {normalized}, Expected: {expected})")

    # 2. Test Caching
    print("\n[2/3] Testing Smart Cache...")
    cache.set("test_key", "test_value")
    if cache.get("test_key") == "test_value":
        print("OK: Cache Set/Get SUCCESS")
    else:
        print("FAIL: Cache Set/Get FAILED")
    
    cache.delete("test_key")
    if cache.get("test_key") is None:
        print("OK: Cache Delete SUCCESS")
    else:
        print("FAIL: Cache Delete FAILED")

    # 3. Test Database Connections (Async)
    print("\n[3/3] Testing Database Connections...")
    try:
        bot_db = BotDatabaseManager()
        fatwa_db = FatwaDatabaseManager()
        
        await bot_db.init_db()
        await fatwa_db.init_db()
        
        print("OK: Database Initialization SUCCESS")
        
        # Test a simple query
        count = await bot_db.get_active_users_count()
        print(f"Stats: Current Subscribers in DB: {count}")
        
    except Exception as e:
        print(f"FAIL: Database Test FAILED ({e})")

    print("\n" + "="*30)
    print("Tests Completed!")
    print("="*30)

if __name__ == "__main__":
    asyncio.run(run_tests())
