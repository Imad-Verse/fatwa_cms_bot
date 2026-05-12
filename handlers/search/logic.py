import json
import logging
import asyncio
from typing import Any
from telegram.ext import ContextTypes
from core.database import FatwaDatabaseManager
from core.bot_db import BotDatabaseManager
from core.config import GROQ_API_KEY, GROQ_MODEL, PROXY_URL
from core.utils import sanitize_input, remove_tashkeel

logger = logging.getLogger(__name__)
db = FatwaDatabaseManager()
bot_db = BotDatabaseManager()

# Constants
AI_SEARCH_MAX_RESULTS = 10
LATEST_LIMIT = 50
POPULAR_LIMIT = 20

_AI_WEIGHT_DEFAULTS = {
    "title_primary": 10,
    "question_primary": 7,
    "answer_primary": 4,
    "title_secondary": 4,
    "question_secondary": 3,
    "answer_secondary": 2,
    "phrase_title": 8,
    "phrase_question": 6,
    "phrase_answer": 3,
}

_AI_WEIGHT_KEYS = {
    "title_primary": "ai_weight_title_primary",
    "question_primary": "ai_weight_question_primary",
    "answer_primary": "ai_weight_answer_primary",
    "title_secondary": "ai_weight_title_secondary",
    "question_secondary": "ai_weight_question_secondary",
    "answer_secondary": "ai_weight_answer_secondary",
    "phrase_title": "ai_weight_phrase_title",
    "phrase_question": "ai_weight_phrase_question",
    "phrase_answer": "ai_weight_phrase_answer",
}

ARABIC_STOPWORDS = {
    "في", "من", "على", "الى", "إلى", "عن", "أن", "إن", "ما", "ماذا", "هل", "لم", "لن", "لا",
    "كان", "كانت", "يكون", "يمكن", "هذا", "هذه", "ذلك", "تلك", "ثم", "او", "أو", "و", "يا",
    "بعد", "قبل", "عند", "مع", "بدون", "بلا", "كل", "بعض", "اكثر", "أكثر", "اقل", "أقل", "جدا",
    "جداً", "حكم", "مسالة", "مسألة", "سؤال", "عندي", "عندنا", "عليه", "عليها", "عليهم", "الذي",
    "التي", "الذين", "اللتي", "اللاتي", "لقد", "انه", "انها", "هناك", "هنا", "اي", "أي",
}

# --- Utility Functions ---

def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))

async def _get_ai_weights() -> dict:
    weights: dict = {}
    for key, setting_key in _AI_WEIGHT_KEYS.items():
        default_val = _AI_WEIGHT_DEFAULTS[key]
        raw = await bot_db.get_setting(setting_key, str(default_val))
        try:
            value = int(raw)
        except Exception:
            value = default_val
        weights[key] = _clamp(value, 1, 40)
    return weights

async def _save_ai_weights(weights: dict):
    for key, setting_key in _AI_WEIGHT_KEYS.items():
        if key not in weights:
            continue
        await bot_db.set_setting(setting_key, str(int(weights[key])))

def _analyze_fatwa_term_hits(fatwa_data: dict, terms: list[str], max_terms_preview: int = 6) -> dict:
    title_text = remove_tashkeel((fatwa_data.get("title") or ""))
    question_text = remove_tashkeel((fatwa_data.get("question") or ""))
    answer_text = remove_tashkeel((fatwa_data.get("answer") or ""))

    matched_title = 0
    matched_question = 0
    matched_answer = 0
    matched_terms: list[str] = []

    for term in terms or []:
        t = remove_tashkeel(term or "").strip()
        if len(t) < 2:
            continue

        hit_t = t in title_text
        hit_q = t in question_text
        hit_a = t in answer_text
        if hit_t:
            matched_title += 1
        if hit_q:
            matched_question += 1
        if hit_a:
            matched_answer += 1
        if (hit_t or hit_q or hit_a) and t not in matched_terms:
            matched_terms.append(t)
            if len(matched_terms) >= max_terms_preview:
                break

    return {
        "matched_title": matched_title,
        "matched_question": matched_question,
        "matched_answer": matched_answer,
        "matched_terms": matched_terms,
    }

def _build_admin_ai_debug_text(debug_data: dict) -> str:
    if not debug_data:
        return ""

    lines = ["🔎 تشخيص البحث بالذكاء الاصطناعي (إداري)"]
    lines.append(f"• النمط: {debug_data.get('mode', 'unknown')}")
    lines.append(
        "• شروط الاستبعاد: "
        f"score >= {debug_data.get('min_score', '-')}, "
        f"primary_matches >= {debug_data.get('min_primary', '-')}"
    )

    primary_terms = debug_data.get("primary_terms") or []
    recall_terms = debug_data.get("recall_terms") or []
    if primary_terms:
        lines.append("• المصطلحات الأساسية: " + ", ".join(primary_terms[:8]))
    if recall_terms:
        lines.append("• مصطلحات البحث الفعلية: " + ", ".join(recall_terms[:8]))

    rows = debug_data.get("rows") or []
    if rows:
        lines.append("• أعلى النتائج:")
        for row in rows[:5]:
            fatwa_num = row.get("fatwa_number")
            score = row.get("score", 0)
            pcount = row.get("primary_match_count", 0)
            mt = row.get("matched_title", 0)
            mq = row.get("matched_question", 0)
            ma = row.get("matched_answer", 0)
            terms = ", ".join(row.get("matched_terms", [])[:4]) or "-"
            lines.append(
                f"  #{fatwa_num} | score={score} | primary={pcount} | "
                f"T/Q/A={mt}/{mq}/{ma} | terms: {terms}"
            )

    return "\n".join(lines)

# --- Data Fetching Functions ---

async def _fetch_popular_fatwas(public_only: bool, limit: int, offset: int, max_total: int | None = None):
    """Fetch popular fatwas with pagination (views desc) - Async."""
    public_sql = "WHERE f.status = 'published'" if public_only else ""
    
    async with await db.get_connection() as conn:
        try:
            async with conn.execute(f"SELECT COUNT(*) FROM fatwas f {public_sql}") as cursor:
                row = await cursor.fetchone()
                total_count = row[0] if row else 0
                
            if max_total is not None:
                total_count = min(total_count, max_total)
                if offset >= max_total:
                    return [], total_count
                limit = min(limit, max_total - offset)
                if limit <= 0:
                    return [], total_count
            
            async with conn.execute(
                f"""
                SELECT f.id
                FROM fatwas f
                {public_sql}
                ORDER BY COALESCE(f.views, 0) DESC, f.fatwa_number DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        except Exception as e:
            logger.error(f"Error fetching popular fatwas: {e}")
            return [], 0

    fatwa_ids = [row[0] for row in rows]
    results = await db.get_fatwas_by_ids(fatwa_ids, public_only=public_only)
    return results, total_count

async def _fetch_general_text_fatwas(
    query_text: str | None,
    public_only: bool,
    limit: int,
    offset: int,
    strict: bool = False,
    max_total: int | None = None,
):
    """بحث شامل محسّن يدعم الجمل الطويلة عبر تفكيكها إلى مفاهيم دلالية - Async."""
    query_text = (query_text or "").strip()
    if not query_text:
        return [], 0

    return await _fetch_contextual_text_fatwas(
        base_terms=[],
        user_query=query_text,
        public_only=public_only,
        limit=limit,
        offset=offset,
        strict=strict,
        max_total=max_total,
    )

def _split_significant_tokens(text: str, max_tokens: int = 8) -> list[str]:
    tokens: list[str] = []
    for token in remove_tashkeel(text).split():
        cleaned = token.strip()
        if len(cleaned) < 3:
            continue
        if cleaned in ARABIC_STOPWORDS:
            continue
        if cleaned.isdigit():
            continue
        if cleaned in tokens:
            continue
        tokens.append(cleaned)
        if len(tokens) >= max_tokens:
            break
    return tokens

def _expand_term_variants(term: str) -> list[str]:
    """توسيع بسيط لصيغ الكلمة العربية لتحسين المطابقة."""
    base = remove_tashkeel(term or "").strip()
    if len(base) < 3:
        return []

    variants: list[str] = []

    def _add(v: str):
        val = (v or "").strip()
        if len(val) < 3:
            return
        if val in ARABIC_STOPWORDS:
            return
        if val not in variants:
            variants.append(val)

    _add(base)
    for pref in ("وال", "بال", "لل", "ال"):
        if base.startswith(pref) and len(base) - len(pref) >= 3:
            _add(base[len(pref):])

    for suf in ("ات", "ون", "ين", "ان", "ة", "ه"):
        if base.endswith(suf) and len(base) - len(suf) >= 4:
            _add(base[:-len(suf)])

    if base.endswith("ة") and len(base) > 4:
        _add(base[:-1] + "ه")

    return variants

def _normalize_query_terms(raw_terms: list[str], fallback_text: str | None = None, max_terms: int = 6) -> list[str]:
    """تنظيف وتوحيد عبارات البحث القادمة من المستخدم أو الذكاء الاصطناعي."""
    normalized: list[str] = []

    def _push(value: str):
        val = remove_tashkeel(sanitize_input(value, max_length=180))
        if len(val) < 2:
            return
        if " " not in val and val in ARABIC_STOPWORDS:
            return
        if val in normalized:
            return
        normalized.append(val)

    for term in raw_terms or []:
        clean = remove_tashkeel(sanitize_input(str(term), max_length=180))
        if len(clean) < 2:
            continue
        if " " in clean and len(clean) <= 90:
            _push(clean)
        for token in _split_significant_tokens(clean, max_tokens=max_terms):
            _push(token)
            if len(normalized) >= max_terms:
                break
        if len(normalized) >= max_terms:
            break

    if not normalized and fallback_text:
        fallback = remove_tashkeel(sanitize_input(fallback_text, max_length=1400))
        if fallback:
            if " " in fallback and len(fallback) <= 120:
                _push(fallback)
            for token in _split_significant_tokens(fallback, max_tokens=max_terms):
                _push(token)
                if len(normalized) >= max_terms:
                    break
            if not normalized:
                _push(fallback)

    return normalized[:max_terms]

def _build_recall_terms(
    base_terms: list[str],
    user_query: str | None = None,
    max_terms: int = 10,
    include_user_tokens: bool = True,
) -> list[str]:
    recall: list[str] = []

    def _push(value: str):
        val = remove_tashkeel(sanitize_input(value, max_length=180)).strip()
        if len(val) < 3:
            return
        if val in ARABIC_STOPWORDS:
            return
        if val in recall:
            return
        recall.append(val)

    for t in base_terms or []:
        _push(t)
        for v in _expand_term_variants(t):
            _push(v)
            if len(recall) >= max_terms:
                return recall

    if include_user_tokens and user_query:
        for token in _split_significant_tokens(user_query, max_tokens=max_terms):
            _push(token)
            for v in _expand_term_variants(token):
                _push(v)
                if len(recall) >= max_terms:
                    return recall

    return recall[:max_terms]

def _build_fts_or_query(terms: list[str], max_terms: int = 8) -> str:
    parts: list[str] = []
    for term in terms or []:
        clean = remove_tashkeel(sanitize_input(str(term), max_length=80))
        clean = clean.replace('"', ' ').replace(":", " ").replace("*", " ")
        clean = clean.replace("(", " ").replace(")", " ").strip()
        clean = " ".join(clean.split())
        if len(clean) < 3:
            continue
        parts.append(f'"{clean}"')
        if len(parts) >= max_terms:
            break
    return " OR ".join(parts)

async def _fetch_contextual_text_fatwas(
    base_terms: list[str],
    user_query: str | None,
    public_only: bool,
    limit: int,
    offset: int,
    strict: bool = False,
    max_total: int | None = None,
    include_debug: bool = False,
    excluded_fatwa_ids: list[int] | None = None,
):
    primary_terms = _normalize_query_terms(base_terms, fallback_text=user_query, max_terms=6)
    include_user_tokens = not (strict and bool(base_terms))
    recall_terms = _build_recall_terms(
        primary_terms,
        user_query=user_query,
        max_terms=8,
        include_user_tokens=include_user_tokens,
    )

    if not recall_terms and user_query:
        fallback = remove_tashkeel(user_query).strip()
        if fallback:
            recall_terms = [fallback]

    if not recall_terms:
        if include_debug:
            return [], 0, {"mode": "none", "strict": bool(strict), "primary_terms": primary_terms, "recall_terms": [], "rows": []}
        return [], 0

    excluded_ids = sorted({int(fid) for fid in (excluded_fatwa_ids or []) if str(fid).isdigit() and int(fid) > 0})
    primary_set = set(primary_terms)
    query_token_count = len(_split_significant_tokens(user_query or "", max_tokens=12))
    primary_count = len(primary_terms)

    if strict:
        if query_token_count >= 5 or primary_count >= 4:
            min_primary_matches = 2
            min_score_required = 20
        elif query_token_count >= 3 or primary_count >= 2:
            min_primary_matches = 1
            min_score_required = 14
        else:
            min_primary_matches = 1 if primary_count > 0 else 0
            min_score_required = 10
    else:
        min_primary_matches = 0
        min_score_required = 5

    weights = await _get_ai_weights()

    async def _run_query(use_fts: bool):
        async with await db.get_connection() as conn:
            try:
                where_clauses = []
                where_params = []
                score_parts = []
                score_params = []
                primary_match_parts = []
                primary_match_params = []
                from_sql = "FROM fatwas f"
                from_params = []
                mode_label = "like"

                if use_fts:
                    fts_query = _build_fts_or_query(recall_terms, max_terms=6)
                    if not fts_query:
                        use_fts = False
                    else:
                        mode_label = "fts"
                        from_sql = "FROM fatwas f JOIN (SELECT rowid AS fid FROM fatwas_fts WHERE fatwas_fts MATCH ? LIMIT 1200) fts ON fts.fid = f.id"
                        from_params = [fts_query]

                if public_only:
                    where_clauses.append("f.status = 'published'")
                if excluded_ids:
                    placeholders = ",".join(["?"] * len(excluded_ids))
                    where_clauses.append(f"f.id NOT IN ({placeholders})")
                    where_params.extend(excluded_ids)

                if not use_fts:
                    term_blocks = []
                    for term in recall_terms:
                        like = f"%{term}%"
                        term_blocks.append("(REMOVE_TASHKEEL(f.title) LIKE ? OR REMOVE_TASHKEEL(f.question) LIKE ? OR REMOVE_TASHKEEL(f.answer) LIKE ?)")
                        where_params.extend([like, like, like])
                    if term_blocks:
                        where_clauses.append("(" + " OR ".join(term_blocks) + ")")

                for term in recall_terms:
                    like = f"%{term}%"
                    title_w = weights["title_primary"] if term in primary_set else weights["title_secondary"]
                    question_w = weights["question_primary"] if term in primary_set else weights["question_secondary"]
                    answer_w = weights["answer_primary"] if term in primary_set else weights["answer_secondary"]
                    score_parts.extend([
                        f"CASE WHEN REMOVE_TASHKEEL(f.title) LIKE ? THEN {title_w} ELSE 0 END",
                        f"CASE WHEN REMOVE_TASHKEEL(f.question) LIKE ? THEN {question_w} ELSE 0 END",
                        f"CASE WHEN REMOVE_TASHKEEL(f.answer) LIKE ? THEN {answer_w} ELSE 0 END",
                    ])
                    score_params.extend([like, like, like])

                for term in primary_terms:
                    like = f"%{term}%"
                    primary_match_parts.append("CASE WHEN (REMOVE_TASHKEEL(f.title) LIKE ? OR REMOVE_TASHKEEL(f.question) LIKE ? OR REMOVE_TASHKEEL(f.answer) LIKE ?) THEN 1 ELSE 0 END")
                    primary_match_params.extend([like, like, like])

                if user_query:
                    phrase = remove_tashkeel(user_query).strip()
                    if 10 <= len(phrase) <= 120:
                        phrase_like = f"%{phrase}%"
                        score_parts.extend([
                            f"CASE WHEN REMOVE_TASHKEEL(f.title) LIKE ? THEN {weights['phrase_title']} ELSE 0 END",
                            f"CASE WHEN REMOVE_TASHKEEL(f.question) LIKE ? THEN {weights['phrase_question']} ELSE 0 END",
                            f"CASE WHEN REMOVE_TASHKEEL(f.answer) LIKE ? THEN {weights['phrase_answer']} ELSE 0 END",
                        ])
                        score_params.extend([phrase_like, phrase_like, phrase_like])

                where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                score_sql = " + ".join(score_parts) if score_parts else "0"
                primary_match_sql = " + ".join(primary_match_parts) if primary_match_parts else "0"

                ranked_sql = f"SELECT f.id, f.fatwa_number, ({score_sql}) AS relevance_score, ({primary_match_sql}) AS primary_match_count {from_sql} {where_sql}"
                filter_clauses = ["relevance_score >= ?"]
                filter_params = [min_score_required]
                if min_primary_matches > 0:
                    filter_clauses.append("primary_match_count >= ?")
                    filter_params.append(min_primary_matches)

                filtered_sql = f"SELECT id, fatwa_number, relevance_score, primary_match_count FROM ({ranked_sql}) ranked WHERE {' AND '.join(filter_clauses)}"
                metric_params = score_params + primary_match_params + from_params + where_params

                if max_total is not None and offset >= max_total:
                    return [], max_total, mode_label
                effective_limit = min(limit, max_total - offset) if max_total is not None else limit
                if effective_limit <= 0:
                    return [], max_total or 0, mode_label

                fetch_limit = effective_limit + 1
                async with conn.execute(f"{filtered_sql} ORDER BY relevance_score DESC, primary_match_count DESC, fatwa_number DESC LIMIT ? OFFSET ?", metric_params + filter_params + [fetch_limit, offset]) as cursor:
                    rows_local = await cursor.fetchall()
                
                has_more = len(rows_local) > effective_limit
                if has_more: rows_local = rows_local[:effective_limit]
                total_count_local = offset + len(rows_local) + (1 if has_more else 0)
                if max_total is not None: total_count_local = min(max_total, total_count_local)
                return rows_local, total_count_local, mode_label
            except Exception as e:
                logger.error(f"Error in _run_query: {e}")
                raise

    fts_available = False
    async with await db.get_connection() as conn_check:
        try:
            async with conn_check.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'") as cursor:
                fts_available = await cursor.fetchone() is not None
        except Exception: pass

    try:
        rows, total_count, query_mode = await _run_query(use_fts=fts_available)
    except Exception as e:
        logger.warning("Search query failed: %s, falling back...", e)
        try:
            rows, total_count, query_mode = await _run_query(use_fts=False)
        except Exception: return [], 0

    fatwa_ids = [int(row["id"]) if hasattr(row, "keys") else int(row[0]) for row in rows]
    results = await db.get_fatwas_by_ids(fatwa_ids, public_only=public_only)
    
    debug_rows = []
    if include_debug:
        # Create a mapping for quick lookup as results might be fewer than IDs if some were unpublished
        results_map = {f['id']: f for f in results}
        for i, row in enumerate(rows):
            fid = fatwa_ids[i]
            fatwa_data = results_map.get(fid)
            if fatwa_data:
                hits = _analyze_fatwa_term_hits(fatwa_data, recall_terms)
                debug_rows.append({
                    "fatwa_id": fid, 
                    "fatwa_number": fatwa_data.get("fatwa_number", fid), 
                    "score": round(float(row["relevance_score"]), 2), 
                    "primary_match_count": int(row["primary_match_count"]), 
                    **hits
                })

    if include_debug:
        return results, total_count, {"mode": query_mode, "strict": bool(strict), "min_score": min_score_required, "min_primary": min_primary_matches, "primary_terms": primary_terms, "recall_terms": recall_terms, "weights": weights, "rows": debug_rows}
    return results, total_count

def _extract_json_payload(raw_text: str):
    if not raw_text: return None
    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try: return json.loads(text)
    except Exception: pass
    for start, end in [("{", "}"), ("[", "]")]:
        s, e = text.find(start), text.rfind(end)
        if s != -1 and e > s:
            try: return json.loads(text[s:e+1])
            except Exception: pass
    return None

def _extract_queries_from_ai_response(content: str, fallback_text: str) -> list[str]:
    candidates = []
    payload = _extract_json_payload(content)
    if isinstance(payload, dict):
        for key in ("queries", "search_queries", "search_terms", "keywords", "phrases"):
            value = payload.get(key)
            if isinstance(value, list): candidates.extend(str(v) for v in value)
            elif isinstance(value, str): candidates.extend(value.replace("،", "\n").replace(",", "\n").splitlines())
        if not candidates:
            for v in payload.values():
                if isinstance(v, str): candidates.append(v)
                elif isinstance(v, list): candidates.extend(str(x) for x in v)
    elif isinstance(payload, list): candidates.extend(str(v) for v in payload)
    if not candidates and content:
        candidates = [line.strip(" -•\t") for line in content.replace("،", "\n").replace(",", "\n").splitlines() if line.strip()]
    return _normalize_query_terms(candidates, fallback_text=fallback_text)

async def _request_ai_query_terms(problem_text: str) -> tuple[list[str], str | None]:
    if not GROQ_API_KEY: return [], "missing_api_key"
    
    from groq import AsyncGroq
    client = AsyncGroq(api_key=GROQ_API_KEY)
    
    system_prompt = "أنت خبير في الفقه الإسلامي ومفتي متمكن. مهمتك هي استخراج مصطلحات البحث الفقهية الأساسية من سؤال المستخدم للبحث عنها في قاعدة بيانات الفتاوى. أجب فقط بتنسيق JSON يحتوي على قائمة 'queries' بالمصطلحات المستخرجة (بحد أقصى 5 مصطلحات)."
    
    try:
        completion = await client.chat.completions.create(
            model=GROQ_MODEL or "llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": problem_text}
            ],
            temperature=0.1,
            max_tokens=220
        )
        content = completion.choices[0].message.content
        queries = _extract_queries_from_ai_response(content, fallback_text=problem_text)
        return (queries, None) if queries else ([], "empty_ai_output")
    except Exception as e:
        logger.error(f"AI search term extraction failed: {e}")
        return [], "request_failed"

async def _generate_ai_answer(user_query: str, fatwas: list[dict]) -> str | None:
    """صياغة إجابة ملخصة بناءً على نتائج البحث المستخرجة (RAG)."""
    if not GROQ_API_KEY: return None
    if not fatwas: return "لم يتم العثور على فتاوى مباشرة في قاعدة البيانات للإجابة على هذا السؤال."

    from groq import AsyncGroq
    client = AsyncGroq(api_key=GROQ_API_KEY)

    # تجهيز السياق من الفتاوى
    context_parts = []
    for f in fatwas[:5]:
        f_num = f.get('fatwa_number', f.get('id', '؟'))
        f_scholar = f.get('scholar_name', 'عالم')
        f_question = f.get('question', '')
        f_answer = f.get('answer', '')
        context_parts.append(f"الفتوى رقم {f_num} (الشيخ {f_scholar}):\nالسؤال: {f_question}\nالجواب: {f_answer}\n---")
    
    context_text = "\n".join(context_parts)
    
    system_prompt = (
        "أنت مساعد شرعي ذكي. مهمتك هي الإجابة على سؤال المستخدم بناءً **فقط** على الفتاوى المقدمة في السياق أدناه.\n"
        "قواعد الإجابة:\n"
        "1. ابدأ الإجابة بملخص فقهي مباشر.\n"
        "2. يجب أن تستند في كل معلومة تذكرها إلى إحدى الفتاوى المقدمة.\n"
        "3. اذكر رقم الفتوى واسم الشيخ عند الاستشهاد (مثال: 'حسب الفتوى رقم 123 للشيخ ابن باز...').\n"
        "4. إذا لم تحتوي الفتاوى المقدمة على إجابة للسؤال، فقل بصراحة: 'عذراً، لم أجد إجابة مباشرة في قاعدة بيانات الفتاوى لهذا السؤال'.\n"
        "5. لا تستخدم معلوماتك الخارجية التي لم ترد في السياق.\n"
        "6. اجعل لغة الإجابة رصينة ومناسبة للمواضيع الشرعية."
    )
    
    user_prompt = f"سؤال المستخدم: {user_query}\n\nالسياق (الفتاوى المتاحة):\n{context_text}"
    
    try:
        completion = await client.chat.completions.create(
            model=GROQ_MODEL or "llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=1200
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"AI answer generation failed: {e}")
        return None

async def _fetch_ai_text_fatwas(query_terms, user_query, public_only, limit, offset, max_total=None, include_debug=False):
    return await _fetch_contextual_text_fatwas(base_terms=query_terms or [], user_query=user_query, public_only=public_only, limit=limit, offset=offset, strict=True, max_total=max_total, include_debug=include_debug)

async def _fetch_smart_fatwas(query_text, use_title, use_text, scholar_ids, public_only, limit, offset):
    query_text = (query_text or '').strip() or None
    normalized_scholar_ids = [int(sid) for sid in (scholar_ids or []) if str(sid).isdigit()]
    
    async with await db.get_connection() as conn:
        try:
            where_clauses = []
            params = []
            if public_only: where_clauses.append("f.status = 'published'")
            if normalized_scholar_ids:
                where_clauses.append(f"f.scholar_id IN ({','.join(['?']*len(normalized_scholar_ids))})")
                params.extend(normalized_scholar_ids)
            if query_text:
                text_clauses = []
                if use_title:
                    text_clauses.append("REMOVE_TASHKEEL(f.title) LIKE ?")
                    params.append(f"%{query_text}%")
                if use_text:
                    text_clauses.extend(["REMOVE_TASHKEEL(f.question) LIKE ?", "REMOVE_TASHKEEL(f.answer) LIKE ?"])
                    params.extend([f"%{query_text}%", f"%{query_text}%"])
                if text_clauses: where_clauses.append('(' + ' OR '.join(text_clauses) + ')')
            
            where_sql = (' WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
            
            async with conn.execute(f"SELECT COUNT(*) FROM fatwas f {where_sql}", params) as cursor:
                row = await cursor.fetchone()
                total_count = row[0] if row else 0
            
            async with conn.execute(f"SELECT f.id FROM fatwas f {where_sql} ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?", params + [limit, offset]) as cursor:
                rows = await cursor.fetchall()
        except Exception as e:
            logger.error(f"Error in _fetch_smart_fatwas: {e}")
            return [], 0

    fatwa_ids = [r[0] for r in rows]
    results = await db.get_fatwas_by_ids(fatwa_ids, public_only=public_only)
    return results, total_count
