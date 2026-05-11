import logging
import aiosqlite
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

class SourcesMixin:
    """Methods for Sources and Source Titles management (Async)."""

    async def _get_or_create_source_id(self, cursor, name: str) -> Optional[int]:
        """Helper to get or create a source ID."""
        await cursor.execute("SELECT id FROM sources WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if row:
            return row["id"]
        await cursor.execute("INSERT INTO sources (name) VALUES (?)", (name,))
        return cursor.lastrowid

    async def _get_or_create_source_title_id(self, cursor, source_name: str, title: str, source_url: str = None, audio_url: str = None) -> Optional[int]:
        """Helper to get or create a source title ID under a specific source."""
        source_id = await self._get_or_create_source_id(cursor, source_name)
        if not source_id:
            return None
        
        await cursor.execute("SELECT id FROM source_titles WHERE source_id = ? AND title = ?", (source_id, title))
        row = await cursor.fetchone()
        if row:
            return row["id"]
        
        await cursor.execute(
            "INSERT INTO source_titles (source_id, title, source_url, audio_url) VALUES (?, ?, ?, ?)",
            (source_id, title, source_url, audio_url)
        )
        return cursor.lastrowid

    async def get_sources(self, limit: int = None, offset: int = 0, search_query: str = None) -> List[Tuple[int, str]]:
        """Retrieve sources from sources table."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT id, name FROM sources WHERE 1=1"
                params = []
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                sql += " ORDER BY name"
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params.extend([limit, offset])
                async with conn.execute(sql, params) as cursor:
                    return [(r["id"], r["name"]) for r in await cursor.fetchall()]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_fatwas_by_source(self, source_id: int, public_only: bool = False, limit: int = 50, offset: int = 0) -> Tuple[List[Dict], int]:
        """Retrieve fatwas linked to a specific source."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                where_clause = "WHERE st.source_id = ?"
                params = [source_id]
                if public_only:
                    where_clause += " AND f.status = 'published'"

                async with conn.execute(f"SELECT COUNT(f.id) FROM fatwas f JOIN source_titles st ON f.source_title_id = st.id {where_clause}", params) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0]

                sql = f"""
                    SELECT f.id FROM fatwas f
                    JOIN source_titles st ON f.source_title_id = st.id
                    {where_clause}
                    ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?
                """
                results = []
                async with conn.execute(sql, params + [limit, offset]) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        fatwa_data = await self.get_fatwa(row[0])
                        if fatwa_data:
                            results.append(fatwa_data)
                return results, total_count
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_sources_count(self, search_query: str = None) -> int:
        """Count sources with search filter."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT COUNT(*) FROM sources WHERE 1=1"
                params = []
                if search_query:
                    sql += " AND REMOVE_TASHKEEL(name) LIKE ?"
                    params.append(f"%{search_query}%")
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def get_source(self, source_id: int) -> Optional[Dict]:
        """Retrieve a specific source by ID."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id, name FROM sources WHERE id = ?", (source_id,)) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def add_source(self, name: str) -> int:
        """Add a new source or return ID if exists."""
        async def _add():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id FROM sources WHERE name = ?", (name,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return row["id"]
                await conn.execute("INSERT INTO sources (name) VALUES (?)", (name,))
                await conn.commit()
                async with conn.execute("SELECT last_insert_rowid()") as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_add)

    async def merge_sources(self, source_id: int, target_source_name: str) -> bool:
        """Merge a source into another by name."""
        async def _merge():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id FROM sources WHERE name = ?", (target_source_name,)) as cursor:
                    target_row = await cursor.fetchone()
                if not target_row:
                    return False
                target_source_id = target_row['id']
                if target_source_id == source_id:
                    return False
                await conn.execute("UPDATE source_titles SET source_id = ? WHERE source_id = ?", (target_source_id, source_id))
                await conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
                await conn.commit()
                return True
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_merge)

    async def update_source(self, source_id: int, new_name: str) -> bool:
        """Update source name."""
        async def _update():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("UPDATE sources SET name = ? WHERE id = ?", (new_name, source_id)) as cursor:
                    await conn.commit()
                    return cursor.rowcount > 0
            except aiosqlite.IntegrityError:
                return False
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_update)

    async def get_source_titles_count(self, source_id: int) -> int:
        """Count titles under a source."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT COUNT(*) FROM source_titles WHERE source_id = ?", (source_id,)) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def delete_source(self, source_id: int) -> bool:
        """Delete a source if it has no linked titles."""
        async def _delete():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT COUNT(*) FROM source_titles WHERE source_id = ?", (source_id,)) as cursor:
                    row = await cursor.fetchone()
                    if row[0] > 0:
                        return False
                async with conn.execute("DELETE FROM sources WHERE id = ?", (source_id,)) as cursor:
                    await conn.commit()
                    return cursor.rowcount > 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_delete)
