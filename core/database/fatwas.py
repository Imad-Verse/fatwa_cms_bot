import logging
import aiosqlite
import re
from typing import Dict, List, Optional, Tuple
from core.utils import remove_tashkeel, cached_async, cache

logger = logging.getLogger(__name__)

class FatwasMixin:
    """Methods for Fatwa CRUD, search, and related logic (Async)."""

    async def add_fatwa(self, data: Dict) -> int:
        """Add a new fatwa."""
        async def _add_fatwa():
            conn = None
            try:
                conn = await self.get_connection()
                # Create a cursor to pass to helpers
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT MAX(fatwa_number) FROM fatwas")
                    row = await cursor.fetchone()
                    max_num = row[0]
                    fatwa_num = (max_num or 0) + 1
                    
                    scholar_name = data.get('scholar_name')
                    scholar_id = await self._get_or_create_scholar_id(cursor, scholar_name)
                    
                    source_name = data.get('source_name')
                    source_title = data.get('source_title') or ""
                    source_url = data.get('source_url')
                    audio_url = data.get('audio_url')
                    source_title_id = await self._get_or_create_source_title_id(
                        cursor, source_name, source_title, source_url=source_url, audio_url=audio_url
                    )
                    if not source_title_id:
                        raise ValueError("Missing source name information for fatwa")
                    
                    answer_text = data.get('answer')
                    question_text = data.get('question')
                    await cursor.execute(
                        '''INSERT INTO fatwas
                            (fatwa_number, title, question, answer, normalized_answer, status, views, scholar_id, source_title_id, created_at)
                            VALUES (?, ?, ?, ?, NORMALIZE_TEXT(?), ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                        (fatwa_num, data['title'], question_text, answer_text, answer_text, data.get('status', 'draft'), 0, scholar_id, source_title_id)
                    )
                    fatwa_id = cursor.lastrowid
                    
                    classifications = data.get('classifications', [])
                    category_ids = set()
                    topic_ids = set()
                    for cls in classifications:
                        if cls.get('category_id'):
                            category_ids.add(cls['category_id'])
                        for t_id in cls.get('topic_ids', []):
                            topic_ids.add(t_id)
                    
                    for cat_id in category_ids:
                        await cursor.execute("INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) VALUES (?, ?)", (fatwa_id, cat_id))
                    for t_id in topic_ids:
                        await cursor.execute("INSERT OR IGNORE INTO fatwa_topics (fatwa_id, topic_id) VALUES (?, ?)", (fatwa_id, t_id))
                    
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern("get_all_fatwas")
                    cache.delete_pattern("get_fatwas_count")
                    return fatwa_id
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_add_fatwa)

    async def get_fatwa_by_number(self, fatwa_number: int) -> Optional[Dict]:
        """Fetch a fatwa by its number."""
        return await self.get_fatwa(fatwa_number=fatwa_number)

    async def delete_fatwa(self, fatwa_id: int) -> bool:
        """Delete a fatwa and its relations."""
        async def _delete():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("DELETE FROM fatwa_topics WHERE fatwa_id = ?", (fatwa_id,))
                await conn.execute("DELETE FROM fatwa_categories WHERE fatwa_id = ?", (fatwa_id,))
                async with conn.execute("DELETE FROM fatwas WHERE id = ?", (fatwa_id,)) as cursor:
                    await conn.commit()
                    # إبطال الكاش
                    cache.delete_pattern(f"get_fatwa:({self}, {fatwa_id})")
                    cache.delete_pattern("get_all_fatwas")
                    cache.delete_pattern("get_fatwas_count")
                    return cursor.rowcount > 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_delete)

    async def get_all_fatwas(self, status: str = None, scholar_id: int = None, source_id: int = None, limit: int = 100, offset: int = 0) -> Tuple[List[Dict], int]:
        """Retrieve fatwas with optional filtering by status, scholar, or source."""
        async def _get_all():
            conn = None
            try:
                conn = await self.get_connection()
                
                where_clauses = []
                params = []
                
                if status:
                    where_clauses.append("f.status = ?")
                    params.append(status)
                if scholar_id:
                    where_clauses.append("f.scholar_id = ?")
                    params.append(scholar_id)
                
                join_clause = ""
                if source_id:
                    join_clause = "JOIN source_titles st ON f.source_title_id = st.id"
                    where_clauses.append("st.source_id = ?")
                    params.append(source_id)
                
                where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                
                count_sql = f"SELECT COUNT(*) FROM fatwas f {join_clause} {where_sql}"
                async with conn.execute(count_sql, params) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0]
                
                sql = f"SELECT f.id FROM fatwas f {join_clause} {where_sql} ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?"
                async with conn.execute(sql, params + [limit, offset]) as cursor:
                    rows = await cursor.fetchall()
                
                results = []
                for row in rows:
                    fatwa_data = await self.get_fatwa(row[0])
                    if fatwa_data:
                        results.append(fatwa_data)
                return results, total_count
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get_all)

    async def get_fatwas_by_ids(self, fatwa_ids: List[int], public_only: bool = False) -> List[Dict]:
        """Retrieve multiple fatwas by their IDs."""
        async def _get():
            if not fatwa_ids: return []
            conn = None
            try:
                conn = await self.get_connection()
                placeholders = ",".join(["?"] * len(fatwa_ids))
                sql = f"SELECT id FROM fatwas WHERE id IN ({placeholders})"
                params = list(fatwa_ids)
                if public_only:
                    sql += " AND status = 'published'"
                async with conn.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                
                found_ids = [row[0] for row in rows]
                id_set = set(found_ids)
                ordered_ids = [fid for fid in fatwa_ids if fid in id_set]
                results = []
                for fid in ordered_ids:
                    fatwa_data = await self.get_fatwa(fid)
                    if fatwa_data:
                        results.append(fatwa_data)
                return results
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def count_fatwas_by_ids(self, fatwa_ids: List[int], public_only: bool = False) -> int:
        """Count how many of the given fatwa IDs exist."""
        async def _count():
            if not fatwa_ids: return 0
            conn = None
            try:
                conn = await self.get_connection()
                placeholders = ",".join(["?"] * len(fatwa_ids))
                sql = f"SELECT COUNT(*) FROM fatwas WHERE id IN ({placeholders})"
                params = list(fatwa_ids)
                if public_only:
                    sql += " AND status = 'published'"
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def get_fatwas_count(self, status: str = None) -> int:
        """Count total fatwas, optionally filtered by status."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                sql = "SELECT COUNT(*) FROM fatwas"
                params = []
                if status:
                    sql += " WHERE status = ?"
                    params.append(status)
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def increment_views(self, fatwa_id: int):
        """Safe increment of views."""
        async def _inc():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("UPDATE fatwas SET views = views + 1 WHERE id = ?", (fatwa_id,))
                await conn.commit()
            except Exception as e:
                logger.warning(f"Failed to increment views for {fatwa_id}: {e}")
            finally:
                if conn: await conn.close()
        try: await _inc()
        except Exception as e: logger.debug(f"Silent increment_views failed: {e}")

    async def increment_views_by(self, fatwa_id: int, delta: int):
        """Safe increment of views by a delta."""
        if not delta or delta <= 0: return
        async def _inc():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute("UPDATE fatwas SET views = views + ? WHERE id = ?", (int(delta), fatwa_id))
                await conn.commit()
            except Exception as e:
                logger.warning(f"Failed to increment views for {fatwa_id} by {delta}: {e}")
            finally:
                if conn: await conn.close()
        try: await _inc()
        except Exception as e: logger.debug(f"Silent increment_views_by failed: {e}")

    async def increment_favorites_count(self, fatwa_id: int, delta: int):
        """Increment/decrement favorites count safely."""
        async def _inc():
            conn = None
            try:
                conn = await self.get_connection()
                await conn.execute(
                    "UPDATE fatwas SET favorites_count = CASE WHEN favorites_count + ? < 0 THEN 0 ELSE favorites_count + ? END WHERE id = ?",
                    (delta, delta, fatwa_id),
                )
                await conn.commit()
            finally:
                if conn: await conn.close()
        await self.execute_with_retry(_inc)

    async def get_top_favorites(self, limit: int = 5) -> List[Dict]:
        """Fetch most favorited fatwas."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute(
                    "SELECT id, title, favorites_count as fav_count FROM fatwas WHERE favorites_count > 0 ORDER BY favorites_count DESC, fatwa_number DESC LIMIT ?",
                    (limit,),
                ) as cursor:
                    return [dict(row) for row in await cursor.fetchall()]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    @cached_async(ttl=1200)
    async def get_fatwa(self, fatwa_id: int = None, fatwa_number: int = None) -> Optional[Dict]:
        """Fetch a single fatwa with all related info."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                conditions = []
                params = []
                if fatwa_id is not None:
                    conditions.append("f.id = ?")
                    params.append(fatwa_id)
                elif fatwa_number is not None:
                    conditions.append("f.fatwa_number = ?")
                    params.append(fatwa_number)
                else: return None
                
                async with conn.execute(f"""
                    SELECT f.*,
                        s.name as scholar_name, s.biography as scholar_biography, s.website as scholar_website,
                        st.title as source_title, st.source_url as source_url, st.audio_url as audio_url,
                        src.name as source_name
                    FROM fatwas f
                    LEFT JOIN scholars s ON f.scholar_id = s.id
                    LEFT JOIN source_titles st ON f.source_title_id = st.id
                    LEFT JOIN sources src ON st.source_id = src.id
                    WHERE {' AND '.join(conditions)}
                """, params) as cursor:
                    row = await cursor.fetchone()
                
                if not row: return None
                fatwa = dict(row)
                
                async with conn.execute("SELECT c.id, c.name, c.type FROM categories c JOIN fatwa_categories fc ON c.id = fc.category_id WHERE fc.fatwa_id = ? ORDER BY c.type, c.name", (fatwa['id'],)) as cursor:
                    category_rows = await cursor.fetchall()
                
                async with conn.execute("""
                    SELECT t.id as topic_id, t.name as topic_name, t.category_id as category_id,
                        c.type as category_type, c.name as category_name
                    FROM fatwa_topics ft
                    JOIN topics t ON ft.topic_id = t.id
                    JOIN categories c ON t.category_id = c.id
                    WHERE ft.fatwa_id = ?
                    ORDER BY c.type, c.name, t.name
                """, (fatwa['id'],)) as cursor:
                    topic_rows = await cursor.fetchall()
                
                topics_by_category = {}
                for r in topic_rows:
                    topics_by_category.setdefault(r['category_id'], []).append({'id': r['topic_id'], 'name': r['topic_name']})
                
                classifications = []
                slots_data = {}
                for cat in category_rows:
                    slot_idx = 1 if cat['type'] == 'fiqh' else 2
                    slots_data.setdefault(slot_idx, {})
                    slots_data[slot_idx][cat['id']] = {'id': cat['id'], 'name': cat['name'], 'topics': topics_by_category.get(cat['id'], [])}
                
                for slot_idx in sorted(slots_data.keys()):
                    for cat_id in sorted(slots_data[slot_idx].keys()):
                        cat = slots_data[slot_idx][cat_id]
                        classifications.append({
                            'category_id': cat['id'], 'category_name': cat['name'],
                            'topic_ids': [t['id'] for t in cat['topics']], 'topic_names': [t['name'] for t in cat['topics']],
                            'slot_index': slot_idx
                        })
                fatwa['classifications'] = classifications
                return fatwa
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def update_fatwa(self, fatwa_id: int, data: Dict) -> bool:
        """Update fatwa fields and relations."""
        async def _update():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.cursor() as cursor:
                    fields_map = {'answer': 'answer', 'title': 'title', 'question': 'question', 'status': 'status', 'fatwa_number': 'fatwa_number'}
                    update_clauses = []
                    values = []
                    if 'scholar_name' in data:
                        scholar_id = await self._get_or_create_scholar_id(cursor, data['scholar_name'])
                        update_clauses.append("scholar_id = ?")
                        values.append(scholar_id)
                    
                    if data.get('source_title_id'):
                        update_clauses.append("source_title_id = ?")
                        values.append(data['source_title_id'])
                    elif any(k in data for k in ['source_name', 'source_title', 'source_url', 'audio_url']):
                        source_name = data.get('source_name')
                        source_title = data.get('source_title')
                        source_url = data.get('source_url')
                        audio_url = data.get('audio_url')
                        
                        if None in [source_name, source_title, source_url, audio_url]:
                            await cursor.execute("SELECT src.name as source_name, st.title as source_title, st.source_url, st.audio_url FROM fatwas f JOIN source_titles st ON f.source_title_id = st.id JOIN sources src ON st.source_id = src.id WHERE f.id = ?", (fatwa_id,))
                            current = await cursor.fetchone()
                            if current:
                                if source_name is None: source_name = current['source_name']
                                if source_title is None: source_title = current['source_title']
                                if source_url is None: source_url = current['source_url']
                                if audio_url is None: audio_url = current['audio_url']
                        
                        if source_name is not None and source_title is not None:
                            st_id = await self._get_or_create_source_title_id(cursor, source_name, source_title, source_url=source_url, audio_url=audio_url)
                            if st_id:
                                update_clauses.append("source_title_id = ?")
                                values.append(st_id)
                    
                    for key, val in data.items():
                        if key in fields_map:
                            if key == 'answer':
                                update_clauses.append("answer = ?")
                                values.append(val)
                                update_clauses.append("normalized_answer = NORMALIZE_TEXT(?)")
                                values.append(val if val is not None else "")
                            else:
                                update_clauses.append(f"{fields_map[key]} = ?")
                                values.append(val)
                    
                    if update_clauses:
                        await cursor.execute(f"UPDATE fatwas SET {', '.join(update_clauses)} WHERE id = ?", values + [fatwa_id])
                    
                    if 'classifications' in data:
                        await cursor.execute("DELETE FROM fatwa_categories WHERE fatwa_id = ?", (fatwa_id,))
                        await cursor.execute("DELETE FROM fatwa_topics WHERE fatwa_id = ?", (fatwa_id,))
                        category_ids, topic_ids = set(), set()
                        for cls in data['classifications']:
                            if cls.get('category_id'): category_ids.add(cls['category_id'])
                            for t_id in cls.get('topic_ids', []): topic_ids.add(t_id)
                        for cat_id in category_ids:
                            await cursor.execute("INSERT OR IGNORE INTO fatwa_categories (fatwa_id, category_id) VALUES (?, ?)", (fatwa_id, cat_id))
                        for t_id in topic_ids:
                            await cursor.execute("INSERT OR IGNORE INTO fatwa_topics (fatwa_id, topic_id) VALUES (?, ?)", (fatwa_id, t_id))
                    
                    await conn.commit()
                    # إبطال كاش الفتوى والبحث
                    cache.delete_pattern(f"get_fatwa:({self}, {fatwa_id})")
                    cache.delete_pattern("get_all_fatwas")
                    return True
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_update)

    async def search_fatwas(self, query_text: str = None, scholar: str = None, category_id: int = None, topic_id: int = None,
                limit: int = 50, offset: int = 0, public_only: bool = True, scope: str = 'all') -> Tuple[List[Dict], int]:
        """Search fatwas."""
        async def _search():
            conn = None
            try:
                conn = await self.get_connection()
                fts_available = False
                if query_text and scope != 'title':
                    try:
                        async with conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'") as cursor:
                            fts_available = await cursor.fetchone() is not None
                    except: fts_available = False

                def _build_where_and_params(include_fts: bool):
                    params = []
                    where_clauses = []
                    if public_only: where_clauses.append("f.status = 'published'")
                    if scholar:
                        where_clauses.append("REMOVE_TASHKEEL(s.name) LIKE ?")
                        params.append(f"%{scholar}%")
                    if category_id:
                        where_clauses.append("EXISTS (SELECT 1 FROM fatwa_categories fc2 WHERE fc2.fatwa_id = f.id AND fc2.category_id = ?)")
                        params.append(category_id)
                    if topic_id:
                        where_clauses.append("EXISTS (SELECT 1 FROM fatwa_topics ft2 WHERE ft2.fatwa_id = f.id AND ft2.topic_id = ?)")
                        params.append(topic_id)
                    if query_text:
                        text_clauses = []
                        if scope == 'title':
                            text_clauses.append("REMOVE_TASHKEEL(f.title) LIKE ?")
                            params.append(f"%{query_text}%")
                        else:
                            if include_fts and fts_available:
                                text_clauses.append("f.id IN (SELECT rowid FROM fatwas_fts WHERE fatwas_fts MATCH ?)")
                                params.append(query_text)
                            text_clauses.append("(REMOVE_TASHKEEL(f.title) LIKE ? OR REMOVE_TASHKEEL(f.question) LIKE ? OR REMOVE_TASHKEEL(f.answer) LIKE ?)")
                            params.extend([f"%{query_text}%"] * 3)
                            for field in ['s.name', 'src.name', 'st.title', 'c.name', 't.name']:
                                text_clauses.append(f"REMOVE_TASHKEEL({field}) LIKE ?")
                                params.append(f"%{query_text}%")
                        where_clauses.append("(" + " OR ".join(text_clauses) + ")")
                    return where_clauses, params

                where_clauses, params = _build_where_and_params(include_fts=fts_available)
                base_sql = "SELECT f.id FROM fatwas f LEFT JOIN scholars s ON f.scholar_id = s.id LEFT JOIN source_titles st ON f.source_title_id = st.id LEFT JOIN sources src ON st.source_id = src.id LEFT JOIN fatwa_categories fc ON f.id = fc.fatwa_id LEFT JOIN categories c ON fc.category_id = c.id LEFT JOIN fatwa_topics ft ON f.id = ft.fatwa_id LEFT JOIN topics t ON ft.topic_id = t.id"

                async def _run_queries(wc, ps):
                    where_sql = (" WHERE " + " AND ".join(wc)) if wc else ""
                    async with conn.execute(f"SELECT COUNT(DISTINCT f.id) FROM fatwas f LEFT JOIN scholars s ON f.scholar_id = s.id LEFT JOIN source_titles st ON f.source_title_id = st.id LEFT JOIN sources src ON st.source_id = src.id LEFT JOIN fatwa_categories fc ON f.id = fc.fatwa_id LEFT JOIN categories c ON fc.category_id = c.id LEFT JOIN fatwa_topics ft ON f.id = ft.fatwa_id LEFT JOIN topics t ON ft.topic_id = t.id {where_sql}", ps) as cursor:
                        row = await cursor.fetchone()
                        total_count = row[0]
                    async with conn.execute(f"{base_sql} {where_sql} GROUP BY f.id ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?", ps + [limit, offset]) as cursor:
                        rows = await cursor.fetchall()
                    
                    results = []
                    for row in rows:
                        fatwa_data = await self.get_fatwa(row[0])
                        if fatwa_data:
                            results.append(fatwa_data)
                    return results, total_count

                try: results, total_count = await _run_queries(where_clauses, params)
                except aiosqlite.OperationalError:
                    if fts_available:
                        wc, ps = _build_where_and_params(include_fts=False)
                        results, total_count = await _run_queries(wc, ps)
                    else: raise
                return results, total_count
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_search)

    async def get_related_fatwas(self, fatwa_id: int, limit: int = 5, public_only: bool = True) -> List[Dict]:
        """Get related fatwas."""
        fatwa = await self.get_fatwa(fatwa_id)
        if not fatwa: return []
        
        def _extract_terms(text: str, max_terms: int = 8) -> List[str]:
            if not text: return []
            cleaned = remove_tashkeel(text)
            cleaned = re.sub(r"[^\w\u0600-\u06FF]+", " ", cleaned, flags=re.UNICODE)
            terms = []
            for t in cleaned.split():
                if len(t.strip()) >= 3 and t.strip() not in terms:
                    terms.append(t.strip())
                    if len(terms) >= max_terms: break
            return terms

        title_terms = _extract_terms(fatwa.get('title'))
        question_terms = _extract_terms(fatwa.get('question'))
        answer_terms = _extract_terms(fatwa.get('answer'))
        topic_ids = []
        for cls in fatwa.get('classifications', []):
            for tid in cls.get('topic_ids', []):
                if tid and int(tid) not in topic_ids: topic_ids.append(int(tid))

        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                related_ids, related_set = [], set()
                fts_available = False
                try:
                    async with conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'") as cursor:
                        fts_available = await cursor.fetchone() is not None
                except: fts_available = False

                def _extend_unique(ids):
                    for fid in ids:
                        if fid not in related_set and fid != fatwa_id:
                            related_set.add(fid); related_ids.append(fid)

                async def _search_by_field(field_name, terms, rem):
                    if rem <= 0 or not terms: return
                    exclude_ids = [fatwa_id] + related_ids
                    placeholders = ",".join(["?"] * len(exclude_ids))
                    where_clauses = [f"f.id NOT IN ({placeholders})"]
                    params = list(exclude_ids)
                    if public_only: where_clauses.append("f.status = 'published'")

                    if fts_available:
                        try:
                            fts_query = " OR ".join([f"{field_name}:\"{t}\"" for t in terms])
                            async with conn.execute(f"SELECT f.id FROM fatwas f JOIN fatwas_fts ON f.id = fatwas_fts.rowid WHERE {' AND '.join(where_clauses + ['fatwas_fts MATCH ?'])} ORDER BY bm25(fatwas_fts) LIMIT ?", params + [fts_query, rem]) as cursor:
                                rows = await cursor.fetchall()
                                _extend_unique([row['id'] for row in rows])
                        except: pass

                    rem = max(0, limit - len(related_ids))
                    if rem > 0:
                        like_clauses, like_params = [], []
                        for term in terms:
                            like_clauses.append(f"REMOVE_TASHKEEL(f.{field_name}) LIKE ?")
                            like_params.append(f"%{term}%")
                        if like_clauses:
                            async with conn.execute(f"SELECT f.id FROM fatwas f WHERE {' AND '.join(where_clauses + ['(' + ' OR '.join(like_clauses) + ')'])} ORDER BY f.fatwa_number DESC LIMIT ?", params + like_params + [rem]) as cursor:
                                rows = await cursor.fetchall()
                                _extend_unique([row['id'] for row in rows])

                await _search_by_field("title", title_terms, limit)
                await _search_by_field("question", question_terms, max(0, limit - len(related_ids)))
                await _search_by_field("answer", answer_terms, max(0, limit - len(related_ids)))
                
                rem = max(0, limit - len(related_ids))
                if rem > 0 and topic_ids:
                    t_placeholders = ",".join(["?"] * len(topic_ids))
                    exclude_ids = [fatwa_id] + related_ids
                    e_placeholders = ",".join(["?"] * len(exclude_ids))
                    async with conn.execute(f"SELECT f.id FROM fatwas f LEFT JOIN (SELECT fatwa_id, COUNT(DISTINCT topic_id) AS topic_score FROM fatwa_topics WHERE topic_id IN ({t_placeholders}) GROUP BY fatwa_id) ts ON f.id = ts.fatwa_id WHERE f.id NOT IN ({e_placeholders}) AND ts.topic_score IS NOT NULL {'AND f.status = \"published\"' if public_only else ''} ORDER BY topic_score DESC, f.fatwa_number DESC LIMIT ?", list(topic_ids) + exclude_ids + [rem]) as cursor:
                        rows = await cursor.fetchall()
                        _extend_unique([row['id'] for row in rows])
                
                return await self.get_fatwas_by_ids(related_ids[:limit], public_only=public_only)
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_search_count(self, query_text: str = None, scholar: str = None, category_id: int = None, topic_id: int = None) -> int:
        """Count search results."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                params = []
                where_clauses = []
                if query_text:
                    text_clauses = []
                    try:
                        async with conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'") as cursor:
                            if await cursor.fetchone():
                                text_clauses.append("f.id IN (SELECT rowid FROM fatwas_fts WHERE fatwas_fts MATCH ?)")
                                params.append(query_text)
                    except: pass
                    text_clauses.append("(REMOVE_TASHKEEL(f.title) LIKE ? OR REMOVE_TASHKEEL(f.question) LIKE ? OR REMOVE_TASHKEEL(f.answer) LIKE ?)")
                    params.extend([f"%{query_text}%"] * 3)
                    for field in ['s.name', 'src.name', 'st.title', 'c.name', 't.name']:
                        text_clauses.append(f"REMOVE_TASHKEEL({field}) LIKE ?")
                        params.append(f"%{query_text}%")
                    where_clauses.append("(" + " OR ".join(text_clauses) + ")")
                if scholar:
                    where_clauses.append("REMOVE_TASHKEEL(s.name) LIKE ?")
                    params.append(f"%{scholar}%")
                if category_id:
                    where_clauses.append("EXISTS (SELECT 1 FROM fatwa_categories fc2 WHERE fc2.fatwa_id = f.id AND fc2.category_id = ?)")
                    params.append(category_id)
                if topic_id:
                    where_clauses.append("EXISTS (SELECT 1 FROM fatwa_topics ft2 WHERE ft2.fatwa_id = f.id AND ft2.topic_id = ?)")
                    params.append(topic_id)
                
                sql = "SELECT COUNT(DISTINCT f.id) FROM fatwas f LEFT JOIN scholars s ON f.scholar_id = s.id LEFT JOIN source_titles st ON f.source_title_id = st.id LEFT JOIN sources src ON st.source_id = src.id LEFT JOIN fatwa_categories fc ON f.id = fc.fatwa_id LEFT JOIN categories c ON fc.category_id = c.id LEFT JOIN fatwa_topics ft ON f.id = ft.fatwa_id LEFT JOIN topics t ON ft.topic_id = t.id WHERE f.status = 'published'"
                if where_clauses: sql += " AND " + " AND ".join(where_clauses)
                async with conn.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def get_fatwas_by_scholar(self, scholar_name: str, public_only: bool = False, limit: int = 50, offset: int = 0) -> Tuple[List[Dict], int]:
        """Fetch fatwas by scholar name."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id FROM scholars WHERE name = ?", (scholar_name,)) as cursor:
                    s_row = await cursor.fetchone()
                if not s_row: return [], 0
                s_id = s_row[0]
                where_clause = "WHERE scholar_id = ?"
                params = [s_id]
                if public_only: where_clause += " AND status = 'published'"
                async with conn.execute(f"SELECT COUNT(*) FROM fatwas {where_clause}", params) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0]
                async with conn.execute(f"SELECT id FROM fatwas {where_clause} ORDER BY fatwa_number DESC LIMIT ? OFFSET ?", params + [limit, offset]) as cursor:
                    rows = await cursor.fetchall()
                
                return [await self.get_fatwa(row[0]) for row in rows], total_count
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_random_published_fatwa(self, category_id: int = None, topic_ids: Optional[List[int]] = None) -> Optional[Dict]:
        """Fetch a random published fatwa."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                params = []
                where_clauses = ["f.status = 'published'", "(f.question IS NOT NULL AND f.question != '')", "(f.answer IS NOT NULL AND f.answer != '')", "(st.audio_url IS NOT NULL AND st.audio_url != '')"]
                if category_id:
                    where_clauses.append("EXISTS (SELECT 1 FROM fatwa_categories fc2 WHERE fc2.fatwa_id = f.id AND fc2.category_id = ?)")
                    params.append(category_id)
                if topic_ids:
                    t_ids = [int(tid) for tid in topic_ids if str(tid).isdigit()]
                    if t_ids:
                        where_clauses.append(f"EXISTS (SELECT 1 FROM fatwa_topics ft2 WHERE ft2.fatwa_id = f.id AND ft2.topic_id IN ({','.join(['?']*len(t_ids))}))")
                        params.extend(t_ids)
                async with conn.execute(f"SELECT f.id FROM fatwas f JOIN source_titles st ON f.source_title_id = st.id WHERE {' AND '.join(where_clauses)} ORDER BY RANDOM() LIMIT 1", params) as cursor:
                    row = await cursor.fetchone()
                return await self.get_fatwa(row[0]) if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_random_fatwa(self, public_only: bool = True, excluded_fatwa_ids: Optional[List[int]] = None) -> Optional[Dict]:
        """Fetch a random fatwa for browsing."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                params = []
                where_clauses = ["(f.answer IS NOT NULL AND TRIM(f.answer) != '')"]
                if public_only: where_clauses.append("f.status = 'published'")
                if excluded_fatwa_ids:
                    e_ids = [int(fid) for fid in excluded_fatwa_ids if str(fid).isdigit()]
                    if e_ids:
                        where_clauses.append(f"f.id NOT IN ({','.join(['?']*len(e_ids))})")
                        params.extend(e_ids)
                async with conn.execute(f"SELECT f.id FROM fatwas f WHERE {' AND '.join(where_clauses)} ORDER BY RANDOM() LIMIT 1", params) as cursor:
                    row = await cursor.fetchone()
                return await self.get_fatwa(row[0]) if row else None
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_fatwas_missing_link(self, link_type: str, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Retrieve fatwas missing source or audio links."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                if link_type == 'source': where_sql = "(st.source_url IS NULL OR st.source_url = '')"
                elif link_type == 'audio': where_sql = "(st.audio_url IS NULL OR st.audio_url = '')"
                else: return []
                async with conn.execute(f"SELECT f.id FROM fatwas f JOIN source_titles st ON f.source_title_id = st.id WHERE {where_sql} ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?", (limit, offset)) as cursor:
                    rows = await cursor.fetchall()
                return [await self.get_fatwa(row[0]) for row in rows]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_missing_link_count(self, link_type: str) -> int:
        """Count fatwas missing specific links."""
        async def _count():
            conn = None
            try:
                conn = await self.get_connection()
                if link_type == 'source': where_sql = "(st.source_url IS NULL OR st.source_url = '')"
                elif link_type == 'audio': where_sql = "(st.audio_url IS NULL OR st.audio_url = '')"
                else: return 0
                async with conn.execute(f"SELECT COUNT(*) FROM fatwas f JOIN source_titles st ON f.source_title_id = st.id WHERE {where_sql}") as cursor:
                    row = await cursor.fetchone()
                    return row[0]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def get_duplicate_fatwas(self, limit: int = 50, offset: int = 0) -> List[Dict]:
        """Retrieve duplicate fatwas by answer text."""
        async def _get():
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("""
                    WITH normalized AS (SELECT id, normalized_answer FROM fatwas WHERE normalized_answer IS NOT NULL AND TRIM(normalized_answer) != ''),
                    duplicate_groups AS (SELECT normalized_answer FROM normalized GROUP BY normalized_answer HAVING COUNT(*) > 1)
                    SELECT n.id FROM normalized n JOIN duplicate_groups d ON d.normalized_answer = n.normalized_answer ORDER BY n.normalized_answer, n.id LIMIT ? OFFSET ?
                """, (limit, offset)) as cursor:
                    rows = await cursor.fetchall()
                ids = [row['id'] for row in rows]
                return await self.get_fatwas_by_ids(ids) if ids else []
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_get)

    async def get_duplicate_count(self) -> int:
        """Count total duplicate fatwas."""
        async def _count():
            conn = await self.get_connection()
            try:
                async with conn.execute("WITH normalized AS (SELECT normalized_answer FROM fatwas WHERE normalized_answer IS NOT NULL AND TRIM(normalized_answer) != '') SELECT COALESCE(SUM(group_count), 0) FROM (SELECT COUNT(*) AS group_count FROM normalized GROUP BY normalized_answer HAVING COUNT(*) > 1) dup") as cursor:
                    row = await cursor.fetchone()
                    return int(row[0]) if row and row[0] is not None else 0
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_count)

    async def find_fatwas_by_exact_answer(self, answer_text: str, limit: int = 5) -> List[Dict]:
        """Find fatwas with exactly the same normalized answer."""
        async def _find():
            if not answer_text or not str(answer_text).strip(): return []
            conn = None
            try:
                conn = await self.get_connection()
                async with conn.execute("SELECT id, fatwa_number, title, status FROM fatwas WHERE normalized_answer = NORMALIZE_TEXT(?) AND normalized_answer IS NOT NULL AND TRIM(normalized_answer) != '' ORDER BY id DESC LIMIT ?", (answer_text, int(limit))) as cursor:
                    rows = await cursor.fetchall()
                # Removed slow fallback to avoid full table scan
                return [{'id': r['id'], 'fatwa_number': r['fatwa_number'], 'title': r['title'], 'status': r['status']} for r in rows]
            finally:
                if conn: await conn.close()
        return await self.execute_with_retry(_find)
