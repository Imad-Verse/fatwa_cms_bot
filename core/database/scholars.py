import logging
from typing import Dict, List, Optional, Tuple
from core.utils import cached_async, cache

logger = logging.getLogger(__name__)

class ScholarsMixin:
    """Methods for Scholar management (Async)."""

    async def _get_or_create_scholar_id(self, cursor, scholar_name: str) -> Optional[int]:
        """Helper to get or create a scholar ID by name."""
        await cursor.execute("SELECT id FROM scholars WHERE name = ?", (scholar_name,))
        row = await cursor.fetchone()
        if row:
            return row["id"]
        await cursor.execute("INSERT INTO scholars (name) VALUES (?)", (scholar_name,))
        return cursor.lastrowid

    @cached_async(ttl=600)
    async def get_scholars(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        """Retrieve scholars (ID and name) from scholars table."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT id, name FROM scholars"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                async with conn.execute(sql, params) as cursor:
                    return [(r['id'], r['name']) for r in await cursor.fetchall()]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_scholars_count(self, search_query: str = None) -> int:
        """Count scholars with optional search query."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT COUNT(*) FROM scholars"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def get_scholars_with_ids(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Dict]:
        """Retrieve scholars with detailed info from scholars table."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT id, name, biography, website FROM scholars"
                params = []
                if search_query:
                    sql += " WHERE REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                async with conn.execute(sql, params) as cursor:
                    return [dict(r) for r in await cursor.fetchall()]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    @cached_async(ttl=1800)
    async def get_scholar_by_id(self, scholar_id: int) -> Optional[Dict]:
        """Retrieve a specific scholar by ID."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id, name, biography, website FROM scholars WHERE id = ?", (scholar_id,)) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def add_scholar(self, name: str) -> Optional[int]:
        """
        Add a new scholar.
        Returns:
            int: The ID of the NEWLY created scholar.
            None: If the scholar already exists (based on NORMALIZE_TEXT).
        """
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
                # التحقق من الوجود باستخدام التطبيع (تجاهل الهمزات والتشكيل)
                async with conn.execute("SELECT id FROM scholars WHERE NORMALIZE_TEXT(name) = NORMALIZE_TEXT(?)", (name,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return None # يشير إلى أن العالم موجود مسبقاً
                
                await conn.execute("INSERT INTO scholars (name) VALUES (?)", (name,))
                await conn.commit()
                # إبطال كاش القوائم
                cache.delete_pattern("get_scholars")
                
                async with conn.execute("SELECT last_insert_rowid()") as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_add)

    async def update_scholar_bio_website(self, scholar_id: int, biography: str, website: str) -> bool:
        """Update scholar biography and website link."""
        async def _update():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "UPDATE scholars SET biography = ?, website = ? WHERE id = ?",
                    (biography, website, scholar_id)
                ) as cursor:
                    await conn.commit()
                    # إبطال كاش العالم والقوائم
                    cache.delete_pattern("get_scholars")
                    cache.delete_pattern(f"get_scholar_by_id:({self}, {scholar_id})")
                    return cursor.rowcount > 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_update)
