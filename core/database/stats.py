import logging
import aiosqlite
import sqlite3
import os
import json
import asyncio
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class StatsMixin:
    """Methods for Statistics, Backup, and Maintenance (Async)."""

    async def get_statistics(self) -> Dict:
        """Fetch general database statistics."""
        async def _stats():
            conn = None
            try:
                conn = await self.get_connection()
                stats = {}
                async with conn.execute("SELECT COUNT(*) as count FROM fatwas") as cursor:
                    row = await cursor.fetchone()
                    stats['total_fatwas'] = row['count']
                
                async with conn.execute("SELECT COUNT(*) as count FROM fatwas WHERE status='published'") as cursor:
                    row = await cursor.fetchone()
                    stats['published_fatwas'] = row['count']
                
                async with conn.execute("SELECT COUNT(*) as count FROM fatwas WHERE status='draft'") as cursor:
                    row = await cursor.fetchone()
                    stats['draft_fatwas'] = row['count']
                
                async with conn.execute("SELECT COUNT(*) as count FROM categories") as cursor:
                    row = await cursor.fetchone()
                    stats['categories'] = row['count']
                
                async with conn.execute("SELECT COUNT(*) as count FROM scholars") as cursor:
                    row = await cursor.fetchone()
                    stats['scholars'] = row['count']
                
                async with conn.execute("SELECT SUM(views) as views FROM fatwas") as cursor:
                    row = await cursor.fetchone()
                    res = row['views']
                    stats['total_views'] = res if res else 0
                
                return stats
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_stats)

    async def count_fatwas_since(self, since_ts: str) -> int:
        """Count fatwas added since a specific timestamp."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "SELECT COUNT(*) as count FROM fatwas WHERE datetime(created_at) >= datetime(?)",
                    (since_ts,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return int(row["count"] if row else 0)
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def backfill_created_at(self) -> int:
        """Backfill created_at for existing rows that are missing it."""
        async def _backfill():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("PRAGMA table_info(fatwas)") as cursor:
                    columns = [row['name'] for row in await cursor.fetchall()]
                
                if 'created_at' not in columns:
                    return 0
                
                async with conn.execute("UPDATE fatwas SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL OR created_at = ''") as cursor:
                    await conn.commit()
                    return cursor.rowcount or 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_backfill)

    async def get_new_fatwa_counts_by_scholar_since(self, since_ts: str) -> List[Dict]:
        """Get counts of newly added published fatwas per scholar."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    """
                    SELECT s.name as scholar_name, COUNT(*) as count
                    FROM fatwas f
                    JOIN scholars s ON f.scholar_id = s.id
                    WHERE f.status = 'published'
                      AND f.created_at >= ?
                    GROUP BY f.scholar_id
                    ORDER BY count DESC, s.name ASC
                    """,
                    (since_ts,),
                ) as cursor:
                    return [dict(row) for row in await cursor.fetchall()]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_total_views(self) -> int:
        """Get total views across all fatwas."""
        stats = await self.get_statistics()
        return stats['total_views']

    async def backup_database(self, backup_path: str) -> bool:
        """Create a backup of the SQLite database (Synchronous part wrapped in executor)."""
        def _do_backup():
            try:
                db_file = getattr(self, 'db_path', getattr(self, 'db_name', 'fatwa.db'))
                if not os.path.exists(db_file):
                    return False
                
                src_conn = sqlite3.connect(db_file, timeout=30.0)
                dst_conn = sqlite3.connect(backup_path, timeout=30.0)
                try:
                    src_conn.backup(dst_conn)
                    return True
                finally:
                    dst_conn.close()
                    src_conn.close()
            except Exception as e:
                logger.error(f"Backup failed: {e}")
                return False
        
        return await asyncio.get_event_loop().run_in_executor(None, _do_backup)

    async def export_json(self, json_path: str) -> bool:
        """Export fatwas database to JSON."""
        try:
            fatwas, _ = await self.get_all_fatwas(limit=10000)
            def _write_json():
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(fatwas, f, ensure_ascii=False, indent=2, default=str)
            
            await asyncio.get_event_loop().run_in_executor(None, _write_json)
            return True
        except Exception as e:
            logger.error(f"JSON Export failed: {e}")
            return False
