import logging
import aiosqlite
from typing import Dict, List, Optional, Tuple
from core.utils import cached_async, cache

logger = logging.getLogger(__name__)

class CategoriesMixin:
    """Methods for Categories and Topics management (Async)."""

    async def add_category(self, name: str, description: str = None, category_type: str = 'fiqh') -> int:
        """Add category with existence check (normalized)."""
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
                # التحقق من الوجود المسبق (تطبيع النص)
                async with conn.execute(
                    "SELECT id FROM categories WHERE NORMALIZE_TEXT(name) = NORMALIZE_TEXT(?) AND type = ?",
                    (name, category_type)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return row['id']

                async with conn.execute("INSERT INTO categories (name, type) VALUES (?, ?)", (name, category_type)) as cursor:
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern("get_categories")
                    return cursor.lastrowid
            except aiosqlite.IntegrityError:
                return 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_add)

    async def update_category(self, cat_id: int, new_name: str = None, category_type: str = None) -> bool:
        """Update category name/type with basic merge support for duplicates."""
        async def _update():
            conn = None
            try:
                conn = await self.get_connection()
                if category_type and not new_name:
                    async with conn.execute("UPDATE categories SET type = ? WHERE id = ?", (category_type, cat_id)) as cursor:
                        await conn.commit()
                        return cursor.rowcount > 0
                if new_name:
                    async with conn.execute("SELECT id FROM categories WHERE name = ? AND id != ?", (new_name, cat_id)) as cursor:
                        existing = await cursor.fetchone()
                    if existing:
                        target_id = existing['id']
                        await conn.execute(
                            "INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) "
                            "SELECT fatwa_id, ? FROM fatwa_categories WHERE category_id = ?",
                            (target_id, cat_id)
                        )
                        await conn.execute("DELETE FROM fatwa_categories WHERE category_id = ?", (cat_id,))
                        await conn.execute("UPDATE topics SET category_id = ? WHERE category_id = ?", (target_id, cat_id))
                        await conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
                        await conn.commit()
                        return True
                    async with conn.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, cat_id)) as cursor:
                        await conn.commit()
                        # إبطال الكاش
                        cache.delete_pattern("get_categories")
                        cache.delete_pattern(f"get_category:({self}, {cat_id})")
                        return cursor.rowcount > 0
                return False
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_update)

    async def delete_category(self, cat_id: int) -> bool:
        """Delete category and detach all related topics and fatwa links."""
        async def _delete():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute(
                    "DELETE FROM fatwa_topics WHERE topic_id IN (SELECT id FROM topics WHERE category_id = ?)",
                    (cat_id,)
                )
                await conn.execute("DELETE FROM topics WHERE category_id = ?", (cat_id,))
                await conn.execute("DELETE FROM fatwa_categories WHERE category_id = ?", (cat_id,))
                async with conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,)) as cursor:
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern("get_categories")
                    cache.delete_pattern(f"get_category:({self}, {cat_id})")
                    return cursor.rowcount > 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_delete)

    async def delete_topic(self, topic_id: int) -> bool:
        """Delete topic and detach it from fatwas."""
        async def _delete():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("DELETE FROM fatwa_topics WHERE topic_id = ?", (topic_id,))
                async with conn.execute("DELETE FROM topics WHERE id = ?", (topic_id,)) as cursor:
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern("get_topics")
                    cache.delete_pattern(f"get_topic:({self}, {topic_id})")
                    return cursor.rowcount > 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_delete)

    async def merge_duplicate_categories(self) -> Dict[str, int]:
        """Merge categories with the same name and type."""
        async def _merge():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("""
                    SELECT name, type, COUNT(id) as cnt, MIN(id) as keep_id
                    FROM categories
                    GROUP BY name, type
                    HAVING cnt > 1
                """) as cursor:
                    duplicates = await cursor.fetchall()
                
                merged_count = 0
                for row in duplicates:
                    name, ctype, keep_id = row['name'], row['type'], row['keep_id']
                    async with conn.execute("SELECT id FROM categories WHERE name = ? AND type = ? AND id != ?", (name, ctype, keep_id)) as cursor:
                        dup_ids = [r['id'] for r in await cursor.fetchall()]
                    for dup_id in dup_ids:
                        await conn.execute(
                            "INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) "
                            "SELECT fatwa_id, ? FROM fatwa_categories WHERE category_id = ?",
                            (keep_id, dup_id)
                        )
                        await conn.execute("DELETE FROM fatwa_categories WHERE category_id = ?", (dup_id,))
                        await conn.execute("UPDATE topics SET category_id = ? WHERE category_id = ?", (keep_id, dup_id))
                        await conn.execute("DELETE FROM categories WHERE id = ?", (dup_id,))
                        merged_count += 1
                await conn.commit()
                return {"categories_merged": merged_count}
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_merge)

    async def merge_duplicate_topics(self) -> Dict[str, int]:
        """Merge topics with the same name within the same category."""
        async def _merge():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("""
                    SELECT name, category_id, COUNT(id) as cnt, MIN(id) as keep_id
                    FROM topics
                    GROUP BY name, category_id
                    HAVING cnt > 1
                """) as cursor:
                    duplicates = await cursor.fetchall()
                
                merged_count = 0
                for row in duplicates:
                    name, cat_id, keep_id = row['name'], row['category_id'], row['keep_id']
                    async with conn.execute("SELECT id FROM topics WHERE name = ? AND category_id = ? AND id != ?", (name, cat_id, keep_id)) as cursor:
                        dup_ids = [r['id'] for r in await cursor.fetchall()]
                    for dup_id in dup_ids:
                        await conn.execute(
                            "INSERT OR IGNORE INTO fatwa_topics (fatwa_id, topic_id) "
                            "SELECT fatwa_id, ? FROM fatwa_topics WHERE topic_id = ?",
                            (keep_id, dup_id)
                        )
                        await conn.execute("DELETE FROM fatwa_topics WHERE topic_id = ?", (dup_id,))
                        await conn.execute("DELETE FROM topics WHERE id = ?", (dup_id,))
                        merged_count += 1
                await conn.commit()
                return {"topics_merged": merged_count}
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_merge)

    @cached_async(ttl=1800)
    async def get_category(self, cat_id: int) -> Optional[Dict]:
        """Fetch a category by ID."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id, name, type FROM categories WHERE id = ?", (cat_id,)) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    @cached_async(ttl=600)
    async def get_categories(self, limit: int = None, offset: int = 0, search_query: str = None, category_type: str = None) -> List[Tuple[int, str]]:
        """Retrieve categories with search and type filtering."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT id, name FROM categories WHERE 1=1"
                params = []
                if category_type:
                    sql += " AND type = ?"
                    params.append(category_type)
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
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

    async def get_categories_count(self, search_query: str = None, category_type: str = None) -> int:
        """Count categories with search and type filtering."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT COUNT(*) FROM categories WHERE 1=1"
                params = []
                if category_type:
                    sql += " AND type = ?"
                    params.append(category_type)
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    @cached_async(ttl=600)
    async def get_topics(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        """Retrieve topics with search."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT id, name FROM topics"
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

    @cached_async(ttl=600)
    async def get_topics_by_category(self, category_id: int, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        """Retrieve topics within a specific category."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT id, name FROM topics WHERE category_id = ?"
                params = [category_id]
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
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

    async def get_topics_count(self, category_id: int, search_query: str = None) -> int:
        """Count topics within a specific category."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT COUNT(*) FROM topics WHERE category_id = ?"
                params = [category_id]
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def add_topic(self, name: str, category_id: int = None) -> int:
        """Add a new topic linked to a category."""
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id FROM topics WHERE name = ? AND category_id = ?", (name, category_id)) as cursor:
                    existing = await cursor.fetchone()
                    if existing:
                        return existing['id']
                async with conn.execute("INSERT INTO topics (name, category_id) VALUES (?, ?)", (name, category_id)) as cursor:
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern("get_topics")
                    return cursor.lastrowid
            except aiosqlite.IntegrityError:
                if category_id is not None:
                    async with conn.execute("SELECT id FROM topics WHERE name = ? AND category_id = ?", (name, category_id)) as cursor:
                        ex = await cursor.fetchone()
                else:
                    async with conn.execute("SELECT id FROM topics WHERE name = ?", (name,)) as cursor:
                        ex = await cursor.fetchone()
                return ex['id'] if ex else 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_add)

    async def get_topic_id_by_name(self, name: str, category_id: Optional[int] = None) -> Optional[int]:
        """Retrieve topic ID by name."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                if category_id is not None:
                    async with conn.execute("SELECT id FROM topics WHERE name = ? AND category_id = ?", (name, category_id)) as cursor:
                        row = await cursor.fetchone()
                else:
                    async with conn.execute("SELECT id FROM topics WHERE name = ?", (name,)) as cursor:
                        row = await cursor.fetchone()
                return row['id'] if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def update_topic(self, topic_id: int, new_name: str) -> bool:
        """Update topic name."""
        async def _update():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("UPDATE topics SET name = ? WHERE id = ?", (new_name, topic_id)) as cursor:
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern("get_topics")
                    cache.delete_pattern(f"get_topic:({self}, {topic_id})")
                    return cursor.rowcount > 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_update)

    @cached_async(ttl=1800)
    async def get_topic(self, topic_id: int) -> Optional[Dict]:
        """Fetch a specific topic by ID."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id, name, category_id FROM topics WHERE id = ?", (topic_id,)) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)
