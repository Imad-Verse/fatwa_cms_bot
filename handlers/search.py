"""


معالجات البحث المتقدمة (handlers/search.py)


----------------------------------


يحتوي على منطق البحث:


- البحث بالعنوان.


- البحث بالعالم.


- البحث بالتصنيف.


- عرض الأحدث والأكثر مشاهدة.


"""


import asyncio
import json
import logging
from urllib import error as urllib_error, request as urllib_request

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup


from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters


from core.database import FatwaDatabaseManager


from core.bot_db import BotDatabaseManager


from core.config import *


from core.utils import (


    sanitize_input,


    format_fatwa_card,


    escape_markdown,


    monitor,


    callback_guard,


    back_to_main_keyboard,


    back_to_search_keyboard,
    remove_tashkeel,


)


from core.keyboards import create_pagination_keyboard, create_fatwa_view_keyboard, back_to_main_keyboard as kb_back_main


db = FatwaDatabaseManager()


bot_db = BotDatabaseManager()
logger = logging.getLogger(__name__)

# حد أقصى لنتائج البحث بالذكاء الاصطناعي (صفحتان × 5 نتائج)
AI_SEARCH_MAX_RESULTS = 10


# Limits for "latest" and "popular" lists


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


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _get_ai_weights() -> dict:
    weights: dict = {}
    for key, setting_key in _AI_WEIGHT_KEYS.items():
        default_val = _AI_WEIGHT_DEFAULTS[key]
        raw = bot_db.get_setting(setting_key, str(default_val))
        try:
            value = int(raw)
        except Exception:
            value = default_val
        weights[key] = _clamp(value, 1, 40)
    return weights


def _save_ai_weights(weights: dict):
    for key, setting_key in _AI_WEIGHT_KEYS.items():
        if key not in weights:
            continue
        bot_db.set_setting(setting_key, str(int(weights[key])))


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


# ==================== Helpers ====================


def _fetch_popular_fatwas(public_only: bool, limit: int, offset: int, max_total: int | None = None):


    """Fetch popular fatwas with pagination (views desc)."""


    conn = db.get_connection()


    c = conn.cursor()


    public_sql = "WHERE f.status = 'published'" if public_only else ""


    try:


        c.execute(f"SELECT COUNT(*) FROM fatwas f {public_sql}")


        total_count = c.fetchone()[0]


        if max_total is not None:


            total_count = min(total_count, max_total)


            if offset >= max_total:


                return [], total_count


            limit = min(limit, max_total - offset)


            if limit <= 0:


                return [], total_count


        c.execute(


            f"""


            SELECT f.id


            FROM fatwas f


            {public_sql}


            ORDER BY COALESCE(f.views, 0) DESC, f.fatwa_number DESC


            LIMIT ? OFFSET ?


            """,


            (limit, offset),


        )


        rows = c.fetchall()


    finally:


        conn.close()


    results = []


    for row in rows:


        fatwa_data = db.get_fatwa(row[0])


        if fatwa_data:


            results.append(fatwa_data)


    return results, total_count


# ==================== Smart Search (البحث المتقدم) ====================


def _get_smart_state(context: ContextTypes.DEFAULT_TYPE) -> dict:


    state = context.user_data.get('smart_search')


    if not isinstance(state, dict):


        state = {'title': False, 'text': False, 'scholars': []}


        context.user_data['smart_search'] = state


    state.setdefault('title', False)


    state.setdefault('text', False)


    state.setdefault('scholars', [])


    return state


def _reset_smart_state(context: ContextTypes.DEFAULT_TYPE):


    context.user_data.pop('smart_search', None)


    context.user_data.pop('smart_search_pending', None)


def _smart_label(label: str, selected: bool) -> str:

    return f"✅ {label}" if selected else label


def _build_smart_search_keyboard(state: dict) -> InlineKeyboardMarkup:


    keyboard = [

        [InlineKeyboardButton(_smart_label('🏷️ العنوان', state.get('title')), callback_data='smart_toggle_title')],

        [InlineKeyboardButton(_smart_label('📝 المحتوى', state.get('text')), callback_data='smart_toggle_text')],

        [InlineKeyboardButton(_smart_label('👤 العالم', bool(state.get('scholars'))), callback_data='smart_select_scholar')],

        [
            InlineKeyboardButton('🔍 بحث الآن', callback_data='smart_search_now'),
            InlineKeyboardButton('🔙 رجوع', callback_data='smart_cancel')
        ],


    ]


    return InlineKeyboardMarkup(keyboard)


async def _render_smart_menu(query, context: ContextTypes.DEFAULT_TYPE):


    state = _get_smart_state(context)


    text = "🎛️ **البحث المتقدم**\n\nقم باختيار طريقة البحث بالضغط على الفلاتر التالية:"


    await query.edit_message_text(text, reply_markup=_build_smart_search_keyboard(state), parse_mode='Markdown')


def _fetch_general_text_fatwas(
    query_text: str | None,
    public_only: bool,
    limit: int,
    offset: int,
    strict: bool = False,
    max_total: int | None = None,
):
    """بحث شامل محسّن يدعم الجمل الطويلة عبر تفكيكها إلى مفاهيم دلالية."""
    query_text = (query_text or "").strip()
    if not query_text:
        return [], 0

    return _fetch_contextual_text_fatwas(
        base_terms=[],
        user_query=query_text,
        public_only=public_only,
        limit=limit,
        offset=offset,
        strict=strict,
        max_total=max_total,
    )


ARABIC_STOPWORDS = {
    "في", "من", "على", "الى", "إلى", "عن", "أن", "إن", "ما", "ماذا", "هل", "لم", "لن", "لا",
    "كان", "كانت", "يكون", "يمكن", "هذا", "هذه", "ذلك", "تلك", "ثم", "او", "أو", "و", "يا",
    "بعد", "قبل", "عند", "مع", "بدون", "بلا", "كل", "بعض", "اكثر", "أكثر", "اقل", "أقل", "جدا",
    "جداً", "حكم", "مسالة", "مسألة", "سؤال", "عندي", "عندنا", "عليه", "عليها", "عليهم", "الذي",
    "التي", "الذين", "اللتي", "اللاتي", "لقد", "انه", "انها", "هناك", "هنا", "اي", "أي",
}


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
    """
    توسيع بسيط لصيغ الكلمة العربية لتحسين المطابقة:
    - إزالة بعض السوابق (الـ، والـ، بالـ...)
    - إزالة بعض اللواحق الشائعة.
    """
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

    # توسيع محافظ حتى لا تتسع النتائج بشكل عشوائي.
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


def _normalize_feedback_query_text(query_text: str | None) -> str:
    """
    توحيد نص السؤال المستخدم كمفتاح للتقييمات (مفيد/غير مرتبط).
    """
    value = sanitize_input(query_text or "", max_length=2000)
    value = remove_tashkeel(value)
    value = " ".join(value.split())
    return value[:1200]


def _build_recall_terms(
    base_terms: list[str],
    user_query: str | None = None,
    max_terms: int = 10,
    include_user_tokens: bool = True,
) -> list[str]:
    """
    يبني حزمة مصطلحات واسعة للاسترجاع:
    - مصطلحات الذكاء الاصطناعي
    - كلمات السؤال الأصلي
    - صيغ موسعة لكل كلمة
    """
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
    """بناء استعلام MATCH بسيط وآمن نسبيًا لـ FTS5."""
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


def _fetch_contextual_text_fatwas(
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
    """
    استرجاع نصي محسّن:
    - recall أعلى عبر مصطلحات موسعة
    - ترتيب بالملاءمة (title/question/answer)
    """
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
            return [], 0, {
                "mode": "none",
                "strict": bool(strict),
                "primary_terms": primary_terms,
                "recall_terms": [],
                "rows": [],
            }
        return [], 0

    excluded_ids = sorted({
        int(fid) for fid in (excluded_fatwa_ids or [])
        if str(fid).isdigit() and int(fid) > 0
    })

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

    weights = _get_ai_weights()

    def _run_query(use_fts: bool):
        conn = db.get_connection()
        c = conn.cursor()
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
                    from_sql = (
                        "FROM fatwas f "
                        "JOIN ("
                        "  SELECT rowid AS fid "
                        "  FROM fatwas_fts "
                        "  WHERE fatwas_fts MATCH ? "
                        "  LIMIT 1200"
                        ") fts ON fts.fid = f.id"
                    )
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
                    term_blocks.append(
                        "("
                        "REMOVE_TASHKEEL(f.title) LIKE ? OR "
                        "REMOVE_TASHKEEL(f.question) LIKE ? OR "
                        "REMOVE_TASHKEEL(f.answer) LIKE ?"
                        ")"
                    )
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
                primary_match_parts.append(
                    "CASE WHEN ("
                    "REMOVE_TASHKEEL(f.title) LIKE ? OR "
                    "REMOVE_TASHKEEL(f.question) LIKE ? OR "
                    "REMOVE_TASHKEEL(f.answer) LIKE ?"
                    ") THEN 1 ELSE 0 END"
                )
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

            where_sql = ""
            if where_clauses:
                where_sql = " WHERE " + " AND ".join(where_clauses)

            score_sql = " + ".join(score_parts) if score_parts else "0"
            primary_match_sql = " + ".join(primary_match_parts) if primary_match_parts else "0"

            ranked_sql = (
                "SELECT "
                "f.id, "
                "f.fatwa_number, "
                f"({score_sql}) AS relevance_score, "
                f"({primary_match_sql}) AS primary_match_count "
                f"{from_sql} {where_sql}"
            )

            filter_clauses = ["relevance_score >= ?"]
            filter_params = [min_score_required]
            if min_primary_matches > 0:
                filter_clauses.append("primary_match_count >= ?")
                filter_params.append(min_primary_matches)

            filtered_sql = (
                "SELECT id, fatwa_number, relevance_score, primary_match_count "
                f"FROM ({ranked_sql}) ranked "
                f"WHERE {' AND '.join(filter_clauses)}"
            )

            metric_params = score_params + primary_match_params + from_params + where_params

            if max_total is not None and offset >= max_total:
                return [], max_total, mode_label

            effective_limit = limit
            if max_total is not None:
                effective_limit = min(limit, max_total - offset)

            if effective_limit <= 0:
                return [], max_total or 0, mode_label

            fetch_limit = effective_limit + 1
            c.execute(
                (
                    f"{filtered_sql} "
                    "ORDER BY relevance_score DESC, primary_match_count DESC, fatwa_number DESC "
                    "LIMIT ? OFFSET ?"
                ),
                metric_params + filter_params + [fetch_limit, offset],
            )
            rows_local = c.fetchall()
            has_more = len(rows_local) > effective_limit
            if has_more:
                rows_local = rows_local[:effective_limit]
            total_count_local = offset + len(rows_local) + (1 if has_more else 0)
            if max_total is not None:
                total_count_local = min(max_total, total_count_local)
            return rows_local, total_count_local, mode_label
        finally:
            conn.close()

    fts_available = False
    conn_check = db.get_connection()
    try:
        c_check = conn_check.cursor()
        c_check.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fatwas_fts'")
        fts_available = c_check.fetchone() is not None
    except Exception:
        fts_available = False
    finally:
        conn_check.close()

    try:
        if fts_available:
            rows, total_count, query_mode = _run_query(use_fts=True)
        else:
            rows, total_count, query_mode = _run_query(use_fts=False)
    except Exception as e:
        logger.warning("FTS primary text query failed: %s, falling back to LIKE...", e)
        try:
            rows, total_count, query_mode = _run_query(use_fts=False)
        except Exception as fallback_e:
            logger.error("Fallback LIKE query failed in _fetch_contextual_text_fatwas: %s", fallback_e)
            if include_debug:
                return [], 0, {
                    "mode": "error",
                    "strict": bool(strict),
                    "primary_terms": primary_terms,
                    "recall_terms": recall_terms,
                    "rows": [],
                }
            return [], 0

    results = []
    debug_rows = []
    for row in rows:
        fatwa_id = int(row["id"]) if isinstance(row, dict) or hasattr(row, "keys") else int(row[0])
        fatwa_data = db.get_fatwa(fatwa_id)
        if fatwa_data:
            results.append(fatwa_data)
            if include_debug:
                hits = _analyze_fatwa_term_hits(fatwa_data, recall_terms)
                debug_rows.append(
                    {
                        "fatwa_id": fatwa_id,
                        "fatwa_number": fatwa_data.get("fatwa_number", fatwa_id),
                        "score": round(float(row["relevance_score"]), 2),
                        "primary_match_count": int(row["primary_match_count"]),
                        **hits,
                    }
                )

    if include_debug:
        return results, total_count, {
            "mode": query_mode,
            "strict": bool(strict),
            "min_score": min_score_required,
            "min_primary": min_primary_matches,
            "primary_terms": primary_terms,
            "recall_terms": recall_terms,
            "weights": weights,
            "rows": debug_rows,
        }

    return results, total_count


def _extract_json_payload(raw_text: str):
    if not raw_text:
        return None

    text = raw_text.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    start_obj = text.find("{")
    end_obj = text.rfind("}")
    if start_obj != -1 and end_obj > start_obj:
        try:
            return json.loads(text[start_obj:end_obj + 1])
        except Exception:
            pass

    start_arr = text.find("[")
    end_arr = text.rfind("]")
    if start_arr != -1 and end_arr > start_arr:
        try:
            return json.loads(text[start_arr:end_arr + 1])
        except Exception:
            pass

    return None


def _extract_queries_from_ai_response(content: str, fallback_text: str) -> list[str]:
    candidates: list[str] = []
    payload = _extract_json_payload(content)

    if isinstance(payload, dict):
        for key in ("queries", "search_queries", "search_terms", "keywords", "phrases"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(str(v) for v in value if v is not None)
            elif isinstance(value, str):
                candidates.extend(value.replace("،", "\n").replace(",", "\n").splitlines())

        if not candidates:
            for value in payload.values():
                if isinstance(value, str):
                    candidates.append(value)
                elif isinstance(value, list):
                    candidates.extend(str(v) for v in value if isinstance(v, (str, int, float)))
    elif isinstance(payload, list):
        candidates.extend(str(v) for v in payload if isinstance(v, (str, int, float)))

    if not candidates and content:
        raw_lines = content.replace("،", "\n").replace(",", "\n").splitlines()
        candidates = [line.strip(" -•\t") for line in raw_lines if line.strip()]

    return _normalize_query_terms(candidates, fallback_text=fallback_text)


def _request_ai_query_terms(problem_text: str) -> tuple[list[str], str | None]:
    """
    يستخدم Groq لاستخراج عبارات بحث مناسبة، ويُعيد:
    - قائمة عبارات البحث
    - كود حالة خطأ (أو None عند النجاح)
    """
    if not GROQ_API_KEY:
        return [], "missing_api_key"

    system_prompt = (
        "أنت خبير في الفقه الإسلامي ومحلل بيانات لمبادرة أرشفة الفتاوى. "
        "مهمتك هي تحليل استفسار المستخدم واستخراج 'خارطة مفاهيم' للبحث. "
        "خطوات العمل: "
        "1. حدد الموضوع الفقهي الرئيسي (مثلاً: طلاق، زكاة، بيوع). "
        "2. استخرج المصطلحات الفقهية الدقيقة المرتبطة بالحالة. "
        "3. أضف مرادفات معاصرة أو تقليدية. "
        "يجب أن تكون النتيجة بتنسيق JSON حصراً: "
        "{\"queries\": [\"مصطلح رئيسي\", \"سياق محدد\", \"مرادف فقهي\", ...]} "
        "الشروط: الحد الأقصى 8 مصطلحات، لغة عربية فصحى، عبارات قصيرة جداً."
    )

    payload = {
        "model": GROQ_MODEL or "llama-3.3-70b-versatile",
        "temperature": 0.1,
        "max_tokens": 220,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": problem_text},
        ],
    }

    req = urllib_request.Request(
        url="https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Accept": "application/json",
            "User-Agent": "FatwaCMSBot/1.0 (+https://t.me/Fatwa_CMS_Bot)",
        },
        method="POST",
    )

    opener = None
    if PROXY_URL:
        opener = urllib_request.build_opener(
            urllib_request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
        )

    try:
        if opener:
            resp_ctx = opener.open(req, timeout=20)
        else:
            resp_ctx = urllib_request.urlopen(req, timeout=20)

        with resp_ctx as resp:
            response_data = json.loads(resp.read().decode("utf-8"))
    except urllib_error.HTTPError as e:
        details = ""
        try:
            details = e.read().decode("utf-8", errors="ignore")[:240]
        except Exception:
            details = ""
        logger.warning("Groq HTTP error (%s): %s", getattr(e, "code", "unknown"), details)

        details_l = details.lower()
        if "1010" in details_l:
            return [], "cloudflare_1010"
        if getattr(e, "code", None) == 401:
            return [], "unauthorized"
        if getattr(e, "code", None) == 429:
            return [], "rate_limited"

        return [], "http_error"
    except Exception as e:
        logger.warning("Groq request failed: %s", e)
        return [], "request_failed"

    choices = response_data.get("choices") or []
    message = choices[0].get("message") if choices else {}
    content = (message or {}).get("content", "") if isinstance(message, dict) else ""

    queries = _extract_queries_from_ai_response(content, fallback_text=problem_text)
    if not queries:
        return [], "empty_ai_output"

    return queries, None


def _fetch_ai_text_fatwas(
    query_terms: list[str],
    user_query: str | None,
    public_only: bool,
    limit: int,
    offset: int,
    max_total: int | None = None,
    include_debug: bool = False,
):
    """بحث AI يعتمد على استرجاع سياقي محسّن (مصطلحات AI + كلمات السؤال)."""
    return _fetch_contextual_text_fatwas(
        base_terms=query_terms or [],
        user_query=user_query,
        public_only=public_only,
        limit=limit,
        offset=offset,
        strict=True,
        max_total=max_total,
        include_debug=include_debug,
    )


def _fetch_smart_fatwas(query_text: str | None, use_title: bool, use_text: bool, scholar_ids: list, public_only: bool, limit: int, offset: int):


    query_text = (query_text or '').strip()


    if not query_text:


        query_text = None


    normalized_scholar_ids = []
    for sid in scholar_ids or []:
        try:
            normalized_scholar_ids.append(int(sid))
        except (TypeError, ValueError):
            continue

    conn = db.get_connection()


    c = conn.cursor()


    try:


        where_clauses = []


        params = []


        if public_only:


            where_clauses.append("f.status = 'published'")


        if normalized_scholar_ids:


            placeholders = ','.join(['?'] * len(normalized_scholar_ids))


            # فلتر العلماء يُطبَّق كقيد AND مع بقية الفلاتر.
            where_clauses.append(f"f.scholar_id IN ({placeholders})")


            params.extend(normalized_scholar_ids)


        if query_text:


            text_clauses = []


            if use_title:


                text_clauses.append("REMOVE_TASHKEEL(f.title) LIKE ?")


                params.append(f"%{query_text}%")


            if use_text:


                text_clauses.append("REMOVE_TASHKEEL(f.question) LIKE ?")


                params.append(f"%{query_text}%")


                text_clauses.append("REMOVE_TASHKEEL(f.answer) LIKE ?")


                params.append(f"%{query_text}%")


            if text_clauses:
                # داخل حقل النص نريد OR: عنوان أو سؤال أو جواب.
                where_clauses.append('(' + ' OR '.join(text_clauses) + ')')


        where_sql = ''


        if where_clauses:


            where_sql = ' WHERE ' + ' AND '.join(where_clauses)


        c.execute(f"SELECT COUNT(*) FROM fatwas f {where_sql}", params)


        total_count = c.fetchone()[0]


        c.execute(


            f"SELECT f.id FROM fatwas f {where_sql} ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?",


            params + [limit, offset]


        )


        rows = c.fetchall()


    finally:


        conn.close()


    results = []


    for row in rows:


        fatwa_data = db.get_fatwa(row[0])


        if fatwa_data:


            results.append(fatwa_data)


    return results, total_count


async def _execute_smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query_text: str | None, is_callback: bool):


    state = _get_smart_state(context)


    use_title = bool(state.get('title'))


    use_text = bool(state.get('text'))


    scholar_ids = list(state.get('scholars') or [])


    public_only = not bot_db.is_admin(update.effective_user.id)


    context.user_data['current_search_state'] = {


        'type': 'smart',


        'params': {


            'query': query_text,


            'use_title': use_title,


            'use_text': use_text,


            'scholars': scholar_ids,


            'public': public_only


        }


    }


    results, total_count = _fetch_smart_fatwas(query_text, use_title, use_text, scholar_ids, public_only, limit=5, offset=0)


    await display_search_results(update, context, results, 'نتائج البحث المتقدم', total_count, is_callback=is_callback, back_callback='search_smart')


# ==================== لوحات المفاتيح والبداية ====================


def create_search_keyboard():
    """إنشاء لوحة مفاتيح خيارات البحث"""
    keyboard = [
        [InlineKeyboardButton("🎛️ بحث متقدم", callback_data="search_smart")],
        [
            InlineKeyboardButton("🔍 بحث بالعنوان", callback_data="search_title"),
            InlineKeyboardButton("🔢 بحث بالرقم", callback_data="search_number")
        ],
        [
            InlineKeyboardButton("🌐 بحث شامل", callback_data="search_all"),
            InlineKeyboardButton("🤖 بالذكاء الاصطناعي", callback_data="search_ai")
        ],
        [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def create_browse_keyboard():
    """إنشاء لوحة مفاتيح خيارات مطالعة الفتاوى"""
    keyboard = [
        [InlineKeyboardButton("🎲 فتوى عشوائية", callback_data="random_fatwa"),InlineKeyboardButton("👤 فتاوى العالم", callback_data="search_scholar")],
        [InlineKeyboardButton("🏷️ الفتاوى المصنفة", callback_data="search_category"),InlineKeyboardButton("📚 مطالعة بالمصادر", callback_data="search_source")],
        [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_search(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """بدء عملية البحث"""


    query = update.callback_query


    await query.answer()


    await query.edit_message_text(


        "🔍 **نظام البحث**\n\nاختر طريقة البحث المناسبة:",


        reply_markup=create_search_keyboard(),


        parse_mode='Markdown'


    )


    return STATE_SEARCH


async def start_browse_fatwas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء شاشة مطالعة الفتاوى"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📖 **مطالعة الفتاوى**\n\nاختر طريقة المطالعة:",
        reply_markup=create_browse_keyboard(),
        parse_mode='Markdown'
    )
    return STATE_SEARCH


# ==================== البحث المتقدم ====================

async def start_smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):

        await query.answer('⏳ يتم فتح البحث المتقدم...', show_alert=False)

        return

    await query.answer()

    _get_smart_state(context)

    await _render_smart_menu(query, context)

    return STATE_SMART_SEARCH


async def smart_toggle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    state = _get_smart_state(context)

    state['title'] = not state.get('title', False)

    await _render_smart_menu(query, context)

    return STATE_SMART_SEARCH


async def smart_toggle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    state = _get_smart_state(context)

    state['text'] = not state.get('text', False)

    await _render_smart_menu(query, context)

    return STATE_SMART_SEARCH


async def smart_open_scholars(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    return await show_smart_scholars_list(update, context, page=0)


async def smart_ai_placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer('البحث بالذكاء الاصطناعي سيفعل في تحديث لاحق', show_alert=True)

    return STATE_SMART_SEARCH


async def search_ai_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):
        await query.answer('⏳ يتم فتح البحث بالذكاء الاصطناعي...', show_alert=False)
        return

    await query.answer()

    await query.edit_message_text(
        "🤖 **البحث بالذكاء الاصطناعي**\n\n"
        "اكتب سؤالك أو صف مشكلتك، وسأحلل النص ثم أبحث لك عن الفتاوى الأقرب في قاعدة البيانات.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 رجوع', callback_data='search_fatwas')]]),
        parse_mode='Markdown'
    )

    return STATE_SEARCH_AI


async def smart_search_now(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):

        await query.answer('⏳ يتم تنفيذ البحث...', show_alert=False)

        return

    await query.answer()

    state = _get_smart_state(context)

    use_title = bool(state.get('title'))

    use_text = bool(state.get('text'))

    has_scholars = bool(state.get('scholars'))


    if not use_title and not use_text and not has_scholars:

        await query.answer('اختر معياراً واحداً على الأقل قبل البحث.', show_alert=True)

        return STATE_SMART_SEARCH


    if use_title or use_text:

        await query.edit_message_text(

            '🎛️ **البحث المتقدم**\n\nأرسل عبارة البحث:',

            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 رجوع', callback_data='search_smart')]]),

            parse_mode='Markdown'

        )

        return STATE_SMART_SEARCH_QUERY


    await _execute_smart_search(update, context, query_text=None, is_callback=True)

    return ConversationHandler.END


async def smart_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    _reset_smart_state(context)

    return await start_search(update, context)


async def show_smart_scholars_list(update_obj, context: ContextTypes.DEFAULT_TYPE, page: int = 0):

    ITEMS_PER_PAGE = 10

    offset = page * ITEMS_PER_PAGE

    context.user_data['smart_sch_page'] = page


    scholars = db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset)

    total_count = db.get_scholars_count()

    state = _get_smart_state(context)

    selected = set(state.get('scholars') or [])


    keyboard = []

    if scholars:

        grid_rows = []

        row = []

        for s_id, scholar_name in scholars:

            label = f"✅ {scholar_name}" if s_id in selected else scholar_name

            row.append(InlineKeyboardButton(label, callback_data=f"smart_sch_toggle_{s_id}"))

            if len(row) == 2:

                grid_rows.append(row)

                row = []

        if row:

            grid_rows.append(row)

        keyboard.extend(grid_rows)


    nav_buttons = []

    if page > 0:

        nav_buttons.append(InlineKeyboardButton('⬅️ السابق', callback_data=f"smart_sch_page_{page-1}"))

    if offset + ITEMS_PER_PAGE < total_count:

        nav_buttons.append(InlineKeyboardButton('➡️ التالي', callback_data=f"smart_sch_page_{page+1}"))

    if nav_buttons:

        keyboard.append(nav_buttons)


    keyboard.append([
        InlineKeyboardButton('🧹 إلغاء الاختيار', callback_data='smart_sch_clear'),
        InlineKeyboardButton('✅ تم الاختيار', callback_data='smart_sch_done')
    ])
    keyboard.append([InlineKeyboardButton('🔙 رجوع', callback_data='smart_sch_back')])


    msg = f"👤 **اختر العلماء** (يمكن اختيار أكثر من واحد)\nالمحدد حالياً: {len(selected)}"

    reply_markup = InlineKeyboardMarkup(keyboard)


    if isinstance(update_obj, Update) and update_obj.callback_query:

        await update_obj.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

    elif hasattr(update_obj, 'edit_message_text'):

        await update_obj.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

    elif isinstance(update_obj, Update) and update_obj.message:

        await update_obj.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


    return STATE_SMART_SEARCH_SCHOLARS


async def handle_smart_scholar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    data = query.data or ''

    state = _get_smart_state(context)

    selected = set(state.get('scholars') or [])


    if data.startswith('smart_sch_page_'):

        page = int(data.split('_')[-1])

        return await show_smart_scholars_list(update, context, page)

    if data.startswith('smart_sch_toggle_'):

        scholar_id = int(data.split('_')[-1])

        if scholar_id in selected:

            selected.remove(scholar_id)

        else:

            selected.add(scholar_id)

        state['scholars'] = sorted(selected)

        page = context.user_data.get('smart_sch_page', 0)

        return await show_smart_scholars_list(update, context, page)

    if data == 'smart_sch_clear':

        state['scholars'] = []

        page = context.user_data.get('smart_sch_page', 0)

        return await show_smart_scholars_list(update, context, page)

    if data == 'smart_sch_done':

        await _render_smart_menu(query, context)

        return STATE_SMART_SEARCH
    if data == 'smart_sch_back':

        await _render_smart_menu(query, context)

        return STATE_SMART_SEARCH


    return STATE_SMART_SEARCH_SCHOLARS


async def perform_smart_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):

    state = _get_smart_state(context)

    if not state.get('title') and not state.get('text'):

        await update.message.reply_text(

            'فعّل البحث بالعنوان أو بالنص أولاً ثم أرسل عبارة البحث.',

            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 رجوع', callback_data='search_smart')]])

        )

        return ConversationHandler.END


    query_text = sanitize_input(update.message.text)

    if not query_text:

        await update.message.reply_text(

            'أرسل عبارة بحث صحيحة.',

            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 رجوع', callback_data='search_smart')]])

        )

        return ConversationHandler.END


    search_query_no_tashkeel = remove_tashkeel(query_text)
    if not search_query_no_tashkeel:
        await update.message.reply_text(
            'أرسل عبارة بحث صحيحة تحتوي على حروف أو أرقام.',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔙 رجوع', callback_data='search_smart')]])
        )
        return ConversationHandler.END

    await _execute_smart_search(update, context, query_text=search_query_no_tashkeel, is_callback=False)

    return ConversationHandler.END


async def perform_search_ai_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = sanitize_input(update.message.text, max_length=2000)
    if not user_text:
        await update.message.reply_text(
            "❌ أرسل وصفاً واضحاً للمشكلة أو السؤال.",
            reply_markup=back_to_search_keyboard()
        )
        return STATE_SEARCH_AI

    normalized_input = _normalize_feedback_query_text(user_text)
    if not normalized_input:
        await update.message.reply_text(
            "❌ أرسل نصاً صالحاً يحتوي على حروف أو أرقام.",
            reply_markup=back_to_search_keyboard()
        )
        return STATE_SEARCH_AI

    status_msg = await update.message.reply_text("⏳ جاري تحليل السؤال بالذكاء الاصطناعي...")

    ai_terms, ai_error = await asyncio.to_thread(_request_ai_query_terms, user_text)
    public_only = not bot_db.is_admin(update.effective_user.id)
    short_query = user_text if len(user_text) <= 60 else f"{user_text[:57]}..."

    # في حال تعذر خدمة الذكاء الاصطناعي: التحويل إلى البحث الشامل مباشرة.
    if ai_error:
        results, total_count = _fetch_general_text_fatwas(
            query_text=normalized_input,
            public_only=public_only,
            limit=5,
            offset=0,
            strict=True,
            max_total=AI_SEARCH_MAX_RESULTS
        )

        context.user_data['current_search_state'] = {
            'type': 'general',
            'params': {
                'query': normalized_input,
                'public': public_only,
                'strict': True,
                'cap': AI_SEARCH_MAX_RESULTS
            }
        }

        try:
            await status_msg.delete()
        except Exception:
            pass

        if ai_error == "cloudflare_1010":
            fallback_msg = (
                "ℹ️ تعذر الوصول إلى خدمة الذكاء الاصطناعي (Cloudflare 1010)، "
                "تم التحويل تلقائياً إلى البحث الشامل."
            )
        elif ai_error == "unauthorized":
            fallback_msg = "ℹ️ مفتاح الذكاء الاصطناعي غير صالح أو منتهي، تم التحويل تلقائياً إلى البحث الشامل."
        elif ai_error == "rate_limited":
            fallback_msg = "ℹ️ خدمة الذكاء الاصطناعي مزدحمة حالياً (Rate limit)، تم التحويل تلقائياً إلى البحث الشامل."
        elif ai_error == "missing_api_key":
            fallback_msg = "ℹ️ مفتاح خدمة الذكاء الاصطناعي غير مضبوط، تم التحويل تلقائياً إلى البحث الشامل."
        else:
            fallback_msg = "ℹ️ تعذر الاتصال بخدمة الذكاء الاصطناعي، تم التحويل تلقائياً إلى البحث الشامل."

        await update.message.reply_text(fallback_msg)
        await display_search_results(
            update,
            context,
            results,
            f"نتائج البحث الشامل: {short_query}",
            total_count,
            page=0,
            back_callback='search_fatwas'
        )
        return ConversationHandler.END

    results, total_count, debug_data = _fetch_ai_text_fatwas(
        query_terms=ai_terms,
        user_query=normalized_input,
        public_only=public_only,
        limit=5,
        offset=0,
        max_total=AI_SEARCH_MAX_RESULTS,
        include_debug=True,
    )

    context.user_data['current_search_state'] = {
        'type': 'ai',
        'params': {
            'terms': ai_terms,
            'user_query': normalized_input,
            'public': public_only,
            'cap': AI_SEARCH_MAX_RESULTS
        }
    }

    try:
        await status_msg.delete()
    except Exception:
        pass

    await display_search_results(
        update,
        context,
        results,
        f"نتائج البحث بالذكاء الاصطناعي: {short_query}",
        total_count,
        page=0,
        back_callback='search_fatwas'
    )

    return ConversationHandler.END


# ==================== البحث الشامل ====================


async def search_all_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """طلب كلمة للبحث الشامل"""


    query = update.callback_query


    await query.answer()


    await query.edit_message_text(


        "🌐 **البحث الشامل**\n\nأرسل كلمة للبحث في (العناوين، الأسئلة، النصوص):",


        reply_markup=back_to_search_keyboard()


    )


    return STATE_SEARCH_ALL


async def perform_search_all(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """تنفيذ البحث الشامل"""


    from core.utils import remove_tashkeel


    search_query = sanitize_input(update.message.text)


    # إزالة التشكيل من نص البحث


    search_query_no_tashkeel = remove_tashkeel(search_query)
    if not search_query_no_tashkeel:
        await update.message.reply_text(
            "❌ أرسل عبارة بحث صحيحة تحتوي على حروف أو أرقام.",
            reply_markup=back_to_search_keyboard()
        )
        return ConversationHandler.END


    # Store State


    public_only = not bot_db.is_admin(update.effective_user.id)


    context.user_data['current_search_state'] = {


        'type': 'general',


        'params': {'query': search_query_no_tashkeel, 'public': public_only}


    }


    # نفس منطق (العنوان + النص): بحث OR في العنوان/السؤال/الجواب
    results, total_count = _fetch_general_text_fatwas(
        search_query_no_tashkeel,
        public_only=public_only,
        limit=5,
        offset=0
    )


    if results:


        await display_search_results(update, context, results, f"نتائج البحث الشامل: {search_query}", total_count, page=0)


    else:


        await update.message.reply_text(


            f"❌ لم يتم العثور على نتائج لـ: {search_query}",


            reply_markup=back_to_search_keyboard()


        )


    return ConversationHandler.END


# ==================== البحث بالرقم ====================


async def search_number_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """طلب رقم الفتوى للبحث"""


    query = update.callback_query


    if query:


        await query.answer()


    # مسح ذاكرة البحث المؤقتة عند العودة للقائمة


    context.user_data['search_cat_query'] = None


    context.user_data['search_topic_query'] = None


    context.user_data['scholar_search_query'] = None


    await query.edit_message_text(


        "🔢 **البحث بالرقم**\n\nأرسل رقم الفتوى:",


        reply_markup=back_to_search_keyboard()


    )


    return STATE_SEARCH_NUMBER


async def perform_search_number(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """تنفيذ البحث بالرقم والعرض المباشر"""


    try:


        fatwa_number = int(update.message.text.strip())


        # البحث بالرقم


        fatwa = db.get_fatwa_by_number(fatwa_number)


        # منع عرض المسودات لغير الإداريين


        if fatwa and not bot_db.is_admin(update.effective_user.id) and fatwa.get('status') != 'published':


            fatwa = None


        if fatwa:


            from core.utils import format_fatwa_card


            # عرض ملخص الفتوى


            card_text = format_fatwa_card(fatwa)


            msg = f"✅ تم العثور على الفتوى رقم {fatwa_number}\n\n{card_text}"


            await update.message.reply_text(


                msg,


                reply_markup=InlineKeyboardMarkup([[


                    InlineKeyboardButton("📖 عرض الفتوى", callback_data=f"view_{fatwa['id']}_search"),


                    InlineKeyboardButton("🔙 رجوع للبحث", callback_data="search_fatwas")


                ]])


            )


        else:


            await update.message.reply_text(


                f"❌ لم يتم العثور على فتوى برقم: {fatwa_number}",


                reply_markup=back_to_search_keyboard()


            )


    except ValueError:


        await update.message.reply_text(


            "❌ الرجاء إدخال رقم صحيح",


            reply_markup=back_to_search_keyboard()


        )


    return ConversationHandler.END


# ==================== البحث بالعنوان ====================


async def search_title_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """طلب عنوان للبحث (بحث عادي)"""


    query = update.callback_query


    await query.answer()


    # مسح ذاكرة البحث المؤقتة عند بدء بحث جديد


    context.user_data['search_cat_query'] = None


    context.user_data['search_topic_query'] = None


    context.user_data['scholar_search_query'] = None


    await query.edit_message_text(


        "📝 **أرسل كلمة البحث في العناوين:**",


        reply_markup=back_to_search_keyboard()


    )


    return STATE_SEARCH_TITLE


async def perform_search_title(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """تنفيذ البحث بالعنوان فقط"""


    from core.utils import remove_tashkeel


    search_query = sanitize_input(update.message.text)


    # إزالة التشكيل


    search_query_no_tashkeel = remove_tashkeel(search_query)
    if not search_query_no_tashkeel:
        await update.message.reply_text(
            "❌ أرسل عبارة بحث صحيحة تحتوي على حروف أو أرقام.",
            reply_markup=back_to_search_keyboard()
        )
        return ConversationHandler.END


    public_only = not bot_db.is_admin(update.effective_user.id)


    context.user_data['current_search_state'] = {


        'type': 'title',


        'params': {'query': search_query_no_tashkeel, 'public': public_only}


    }


    results, total_count = db.search_fatwas(search_query_no_tashkeel, public_only=public_only, scope='title', limit=5, offset=0)


    if results:


        await display_search_results(update, context, results, f"نتائج البحث بالعنوان: {search_query}", total_count, page=0)


    else:


        await update.message.reply_text(


            f"❌ لم يتم العثور على نتائج في العناوين لـ: {search_query}",


            reply_markup=back_to_search_keyboard()


        )


    return ConversationHandler.END


# ==================== البحث بالعالم ====================


async def search_scholar(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """بدء البحث بالعالم (عرض القائمة)"""


    query = update.callback_query


    # منع النقرات المتكررة السريعة على نفس الزر


    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):


        await query.answer("⏳ جاري فتح قائمة العلماء، انتظر لحظة...", show_alert=False)


        return


    await query.answer()


    # مسح ذاكرة البحث المؤقتة عند بدء بحث جديد


    context.user_data['search_cat_query'] = None


    context.user_data['search_topic_query'] = None


    context.user_data['scholar_search_query'] = None


    return await show_scholars_list(update, context, page=0)


async def show_scholars_list(update_obj, context, page=0):


    ITEMS_PER_PAGE = 10


    offset = page * ITEMS_PER_PAGE


    scholars = db.get_scholars(limit=ITEMS_PER_PAGE, offset=offset)


    total_count = db.get_scholars_count()


    keyboard = []


    # 2-Column Grid Layout


    if scholars:


        grid_rows = []


        row = []


        for s_id, scholar_name in scholars:


            # Use ID in callback data to avoid exceeding 64 bytes
            # Include page number for correct back navigation
            btn = InlineKeyboardButton(f"👤 {scholar_name}", callback_data=f"sel_scholar_id_{s_id}_{page}")


            row.append(btn)


            if len(row) == 2:


                grid_rows.append(row)


                row = []


        if row:


            grid_rows.append(row)


        keyboard.extend(grid_rows)


    nav_buttons = []


    if page > 0:


        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sch_page_{page-1}"))


    if offset + ITEMS_PER_PAGE < total_count:


        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sch_page_{page+1}"))


    if nav_buttons:


        keyboard.append(nav_buttons)


    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_fatwas")])


    msg = f"👤 **اختر العالم** (صفحة {page+1}):"


    reply_markup = InlineKeyboardMarkup(keyboard)


    if isinstance(update_obj, Update) and update_obj.callback_query:


        await update_obj.callback_query.edit_message_text(msg, reply_markup=reply_markup)


    elif hasattr(update_obj, 'edit_message_text'):


        await update_obj.edit_message_text(msg, reply_markup=reply_markup)


    elif isinstance(update_obj, Update) and update_obj.message:


        await update_obj.message.reply_text(msg, reply_markup=reply_markup)


    return STATE_SEARCH_SCHOLAR


async def handle_scholar_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):


    query = update.callback_query


    await query.answer()


    data = query.data


    if data.startswith("sch_page_"):


        page = int(data.split('_')[-1])


        await show_scholars_list(update, context, page)


        return STATE_SEARCH_SCHOLAR


    elif data.startswith("sel_scholar_id_"):


        parts = data.split('_')
        scholar_id = int(parts[3])
        page = int(parts[4]) if len(parts) > 4 else 0


        scholar = db.get_scholar_by_id(scholar_id)


        scholar_name = scholar.get('name') if scholar else None


        if not scholar_name:


            await query.edit_message_text(


                "❌ لم يتم العثور على العالم.",


                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للبحث", callback_data="search_scholar")]])


            )


            return ConversationHandler.END


        public_only = not bot_db.is_admin(update.effective_user.id)


        context.user_data['current_search_state'] = {


            'type': 'scholar',


            'params': {'scholar': scholar_name, 'public': public_only}


        }


        results, total_count = db.get_fatwas_by_scholar(scholar_name, public_only=public_only, limit=5, offset=0)


        if results:


            title = f"فتاوى الشيخ: {scholar_name} [{total_count} فتوى]"


            # Pass the correct back callback to return to the scholar list page
            await display_search_results(update, context, results, title, total_count, is_callback=True, back_callback=f"sch_page_{page}")

            return STATE_SEARCH_SCHOLAR # Keep conversation alive for pagination/back


        else:


            await query.edit_message_text(


                f"❌ لا توجد فتاوى منشورة للشيخ: {scholar_name}",


                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للبحث", callback_data="search_scholar")]])


            )


        return ConversationHandler.END


    elif data.startswith("sel_scholar_"):


        # دعم خلفي للرسائل القديمة التي تستخدم الاسم في callback_data


        scholar_name = data.replace("sel_scholar_", "")


        public_only = not bot_db.is_admin(update.effective_user.id)


        context.user_data['current_search_state'] = {


            'type': 'scholar',


            'params': {'scholar': scholar_name, 'public': public_only}


        }


        results, total_count = db.get_fatwas_by_scholar(scholar_name, public_only=public_only, limit=5, offset=0)


        if results:


            title = f"فتاوى الشيخ: {scholar_name} [{total_count} فتوى]"


            await display_search_results(update, context, results, title, total_count, is_callback=True)

            return STATE_SEARCH_SCHOLAR


        else:


            await query.edit_message_text(


                f"❌ لا توجد فتاوى منشورة للشيخ: {scholar_name}",


                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع للبحث", callback_data="search_scholar")]])


            )


        return ConversationHandler.END


async def handle_search_topic_query(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """استلام نص البحث عن موضوع"""


    query_text = update.message.text.strip()


    context.user_data['search_topic_query'] = query_text


    cat_id = context.user_data.get('search_cat_id')


    await show_topics_list(update, context, cat_id, page=0, search_query=query_text)


    return STATE_SEARCH_TOPIC


# ==================== البحث بالتصنيف (تصنيف -> موضوع -> فتاوى) ====================


# ==================== البحث بالمصادر ====================


async def search_source(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """بدء البحث بالمصادر (عرض القائمة)"""


    query = update.callback_query


    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):


        await query.answer("⏳ جاري فتح قائمة المصادر، انتظر لحظة...", show_alert=False)


        return


    await query.answer()


    context.user_data['source_search_query'] = None


    return await show_sources_list(update, context, page=0)


async def show_sources_list(update_obj, context, page=0, search_query=None):


    ITEMS_PER_PAGE = 8


    offset = page * ITEMS_PER_PAGE


    if search_query is None:


        search_query = context.user_data.get('source_search_query')


    sources = db.get_sources(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query)


    total_count = db.get_sources_count(search_query=search_query)


    context.user_data['source_list_page'] = page


    keyboard = []


    row = []


    for s_id, s_name in sources:


         row.append(InlineKeyboardButton(f"📚 {s_name}", callback_data=f"sel_source_{s_id}"))


         if len(row) == 2:


             keyboard.append(row)


             row = []


    if row:


        keyboard.append(row)


    nav_buttons = []


    if page > 0:


        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"src_page_{page-1}"))


    if offset + ITEMS_PER_PAGE < total_count:


        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"src_page_{page+1}"))


    if nav_buttons:


        keyboard.append(nav_buttons)


    keyboard.append([InlineKeyboardButton("🔍 بحث باسم المصدر", callback_data="search_source_query")])


    if search_query:


        keyboard.append([InlineKeyboardButton("🧹 مسح البحث", callback_data="clear_source_search")])


    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_fatwas")])


    title = "📚 **اختر مصدرًا**"


    if search_query:


        safe_query = escape_markdown(search_query)


        title += f"\n🔍 نتائج البحث عن: `{safe_query}`"


    reply_markup = InlineKeyboardMarkup(keyboard)


    if isinstance(update_obj, Update) and update_obj.callback_query:


        await update_obj.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')


    elif hasattr(update_obj, 'edit_message_text'):


        await update_obj.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')


    elif isinstance(update_obj, Update) and update_obj.message:


        await update_obj.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')


    return STATE_SEARCH_SOURCE


async def handle_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):


    query = update.callback_query


    await query.answer()


    data = query.data


    if data.startswith("src_page_"):


        page = int(data.split('_')[-1])


        return await show_sources_list(update, context, page)


    if data == "search_source_query":


        await query.edit_message_text(


            "🔍 **بحث باسم المصدر**\n\nأرسل كلمة البحث:",


            reply_markup=InlineKeyboardMarkup([


                [InlineKeyboardButton("🔙 رجوع للمصادر", callback_data="search_source")],


                [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="back_main")]


            ]),


            parse_mode='Markdown'


        )


        return STATE_SEARCH_SOURCE_QUERY


    if data == "clear_source_search":


        context.user_data['source_search_query'] = None


        return await show_sources_list(update, context, page=0, search_query=None)


    if data.startswith("sel_source_"):


        source_id = int(data.split('_')[-1])


        source = db.get_source(source_id)


        source_name = source['name'] if source else "غير معروف"


        public_only = not bot_db.is_admin(update.effective_user.id)


        context.user_data['current_search_state'] = {


            'type': 'source',


            'params': {'id': source_id, 'public': public_only}


        }


        results, total_count = db.get_fatwas_by_source(source_id, public_only=public_only, limit=5, offset=0)


        back_page = context.user_data.get('source_list_page', 0)


        back_callback = f"src_page_{back_page}"


        if results:


            await display_search_results(update, context, results, f"فتاوى المصدر: {source_name}", total_count, is_callback=True, back_callback=back_callback)


        else:


            markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data=back_callback)]])


            await query.edit_message_text("❌ لا توجد فتاوى ضمن هذا المصدر.", reply_markup=markup)


        return STATE_SEARCH_SOURCE


async def show_scholar_fatwas_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """عرض فتاوى العالم عبر زر من عرض الفتوى."""


    query = update.callback_query


    await query.answer()


    data = query.data or ""


    try:
        # Supports both:
        # - scholar_fatwas_{scholar_id}
        # - scholar_fatwas_{scholar_id}_{origin_fatwa_id}
        parts = data.split('_')
        scholar_id = int(parts[2])
        origin_fatwa_id = int(parts[3]) if len(parts) > 3 else None


    except (IndexError, ValueError):


        await query.edit_message_text("❌ لم يتم العثور على العالم.", reply_markup=back_to_main_keyboard())


        return


    scholar = db.get_scholar_by_id(scholar_id)


    scholar_name = scholar.get('name') if scholar else None


    if not scholar_name:


        await query.edit_message_text("❌ لم يتم العثور على العالم.", reply_markup=back_to_main_keyboard())


        return


    public_only = not bot_db.is_admin(update.effective_user.id)


    context.user_data['current_search_state'] = {


        'type': 'scholar',


        'params': {'scholar': scholar_name, 'public': public_only}


    }


    results, total_count = db.get_fatwas_by_scholar(scholar_name, public_only=public_only, limit=5, offset=0)


    title = f"فتاوى الشيخ: {scholar_name} [{total_count} فتوى]"
    back_callback = f"view_{origin_fatwa_id}" if origin_fatwa_id else "back_main"
    back_label = "🔙 رجوع للفتوى" if origin_fatwa_id else None
    await display_search_results(
        update,
        context,
        results,
        title,
        total_count,
        is_callback=True,
        back_callback=back_callback,
        back_label=back_label
    )

async def handle_search_source_query(update: Update, context: ContextTypes.DEFAULT_TYPE):


    query_text = sanitize_input(update.message.text.strip())


    context.user_data['source_search_query'] = query_text


    await show_sources_list(update, context, page=0, search_query=query_text)


    return STATE_SEARCH_SOURCE


async def search_category(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """بدء البحث بالتصنيف (عرض القائمة)"""


    query = update.callback_query


    # منع النقرات المتكررة السريعة على نفس الزر


    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):


        await query.answer("⏳ جاري فتح قائمة التصنيفات، انتظر لحظة...", show_alert=False)


        return


    await query.answer()


    # مسح ذاكرة البحث المؤقتة عند بدء بحث جديد


    context.user_data['search_cat_query'] = None


    context.user_data['search_topic_query'] = None


    context.user_data['scholar_search_query'] = None


    context.user_data['search_cat_type'] = None


    return await show_categories_list(update, context, page=0)


async def show_categories_list(update_obj, context, page=0, search_query=None):


    ITEMS_PER_PAGE = 10


    offset = page * ITEMS_PER_PAGE


    if search_query is None:


        search_query = context.user_data.get('search_cat_query')


    cat_type = context.user_data.get('search_cat_type')


    cats = db.get_categories(limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query, category_type=cat_type) # [(id, name)]


    total_count = db.get_categories_count(search_query=search_query, category_type=cat_type)


    keyboard = []


    type_row = [


        InlineKeyboardButton("📋 الكل" if cat_type is None else "الكل", callback_data="search_cat_type_all"),


        InlineKeyboardButton("🕌 فقهي" if cat_type == 'fiqh' else "فقهي", callback_data="search_cat_type_fiqh"),


        InlineKeyboardButton("📂 موضوعي" if cat_type == 'topic' else "موضوعي", callback_data="search_cat_type_topic")


    ]


    keyboard.append(type_row)


    # 2-Column Grid Layout


    if cats:


        grid_rows = []


        row = []


        for cid, name in cats:


            btn = InlineKeyboardButton(f"🏷️ {name}", callback_data=f"sel_cat_{cid}")


            row.append(btn)


            if len(row) == 2:


                grid_rows.append(row)


                row = []


        if row:


            grid_rows.append(row)


        keyboard.extend(grid_rows)


    nav_buttons = []


    if page > 0:


        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"cat_page_{page-1}"))


    if offset + ITEMS_PER_PAGE < total_count:


        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"cat_page_{page+1}"))


    if nav_buttons:


        keyboard.append(nav_buttons)


    keyboard.append([InlineKeyboardButton("🔍 بحث عن تصنيف", callback_data="search_cat_query")])


    keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="browse_fatwas")])


    type_label = ""


    if cat_type == 'fiqh':


        type_label = " (فقهي)"


    elif cat_type == 'topic':


        type_label = " (موضوعي)"


    msg = f"🏷️ **اختر التصنيف** (صفحة {page+1}){type_label}:"


    reply_markup = InlineKeyboardMarkup(keyboard)


    if isinstance(update_obj, Update) and update_obj.callback_query:


        await update_obj.callback_query.edit_message_text(msg, reply_markup=reply_markup)


    elif hasattr(update_obj, 'edit_message_text'):


        await update_obj.edit_message_text(msg, reply_markup=reply_markup)


    elif isinstance(update_obj, Update) and update_obj.message:


        await update_obj.message.reply_text(msg, reply_markup=reply_markup)


    return STATE_SEARCH_CATEGORY


async def handle_category_search_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):


    query = update.callback_query


    await query.answer()


    data = query.data


    if data == "search_cat_query":


        # تهيئة البيانات ومسح الذاكرة المؤقتة للبحث


        context.user_data['search_cat_query'] = None


        context.user_data['search_topic_query'] = None


        context.user_data['scholar_search_query'] = None


        await query.edit_message_text("🔍 أرسل اسم التصنيف (الفقهي أو الموضوعي) للبحث عنه:", reply_markup=InlineKeyboardMarkup([


            [InlineKeyboardButton("🔙 إلغاء البحث", callback_data="search_category")],


            [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]


        ]))


        return STATE_SEARCH_CAT_SEARCH


    elif data.startswith("search_cat_type_"):


        cat_type = data.split("search_cat_type_")[-1]


        if cat_type == "all":


            context.user_data['search_cat_type'] = None


        elif cat_type in ("fiqh", "topic"):


            context.user_data['search_cat_type'] = cat_type


        return await show_categories_list(update, context, page=0)


    elif data.startswith("cat_page_"):


        page = int(data.split('_')[-1])


        return await show_categories_list(update, context, page)


    elif data.startswith("sel_cat_"):


        cat_id = int(data.split('_')[-1])


        context.user_data['search_cat_id'] = cat_id


        # التأكد من وجود مواضيع داخل هذا التصنيف


        topics_count = db.get_topics_count(cat_id)


        if topics_count > 0:


            # إذا وجدت مواضيع، نعرض قائمة المواضيع أولاً


            return await show_topics_list(update, context, cat_id, page=0)


        else:


            # إذا لم توجد مواضيع، نعرض الفتاوى مباشرة


            return await fetch_and_display_cat_fatwas(update, context, cat_id)


async def handle_search_cat_query(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """استلام نص البحث عن التصنيف"""


    query_text = update.message.text.strip()


    context.user_data['search_cat_query'] = query_text


    await show_categories_list(update, context, page=0, search_query=query_text)


    return STATE_SEARCH_CATEGORY


async def fetch_and_display_cat_fatwas(update_obj, context, cat_id, topic_id=None):


    """مساعد لجلب وعرض الفتاوى لتصنيف أو موضوع with Pagination"""


    public_only = not bot_db.is_admin(update_obj.effective_user.id)


    public_sql = " AND f.status = 'published'" if public_only else ""


    # Store State


    context.user_data['current_search_state'] = {


        'type': 'category_custom',


        'params': {'cat_id': cat_id, 'topic_id': topic_id, 'public': public_only}


    }


    conn = db.get_connection()


    c = conn.cursor()


    # تحديد زر الرجوع المناسب


    back_callback = "search_category" # افتراضي


    join_clause = ""


    where_clause = ""


    params = []


    if topic_id == "none": # الفتاوى التي بدون موضوع ضمن هذا التصنيف


        back_callback = f"sel_cat_{cat_id}"


        join_clause = "JOIN fatwa_categories fc ON f.id = fc.fatwa_id"


        where_clause = f"""


            WHERE fc.category_id = ? {public_sql}


              AND NOT EXISTS (


                SELECT 1


                FROM fatwa_topics ft


                JOIN topics t ON ft.topic_id = t.id


                WHERE ft.fatwa_id = f.id AND t.category_id = ?


              )


        """


        params = [cat_id, cat_id]


    elif topic_id:


        back_callback = f"sel_cat_{cat_id}"


        join_clause = "JOIN fatwa_topics ft ON f.id = ft.fatwa_id"


        where_clause = f"WHERE ft.topic_id = ? {public_sql}"


        params = [topic_id]


    else:


        # عرض فتاوى التصنيف مباشرة


        back_callback = "search_category"


        join_clause = "JOIN fatwa_categories fc ON f.id = fc.fatwa_id"


        where_clause = f"WHERE fc.category_id = ? {public_sql}"


        params = [cat_id]


    # Count


    c.execute(f"SELECT COUNT(DISTINCT f.id) FROM fatwas f {join_clause} {where_clause}", params)


    total_count = c.fetchone()[0]


    # Data (Page 0)


    sql = f"""


        SELECT f.id


        FROM fatwas f


        {join_clause}


        {where_clause}


        GROUP BY f.id


        ORDER BY f.fatwa_number DESC LIMIT 5 OFFSET 0


    """


    c.execute(sql, params)


    results = []


    for row in c.fetchall():


        fatwa_data = db.get_fatwa(row[0])


        if fatwa_data:


            results.append(fatwa_data)


    conn.close()


    # العنوان


    all_cats = dict(db.get_categories())


    cat_name = all_cats.get(cat_id, "التصنيف")


    title = f"فتاوى: {cat_name}"


    if topic_id and topic_id != "none":


        topics = db.get_topics_by_category(cat_id)


        topic_name = dict(topics).get(topic_id, "الموضوع")


        title = f"فتاوى: {cat_name} -> {topic_name}"


    elif topic_id == "none":


        title = f"فتاوى {cat_name} (بدون موضوع)"


    await display_search_results(update_obj, context, results, title, total_count, is_callback=True, page=0, back_callback=back_callback)


    return STATE_SEARCH_CATEGORY


async def show_topics_list(update_obj, context, cat_id, page=0, search_query=None):


    ITEMS_PER_PAGE = 10


    offset = page * ITEMS_PER_PAGE


    if search_query is None:


        search_query = context.user_data.get('search_topic_query')


    topics = db.get_topics_by_category(cat_id, limit=ITEMS_PER_PAGE, offset=offset, search_query=search_query) # Should return [(id, name)]


    total_count = db.get_topics_count(cat_id, search_query=search_query)


    # الحصول على اسم التصنيف


    cat_name = dict(db.get_categories()).get(cat_id, "التصنيف")


    safe_cat_name = escape_markdown(cat_name)


    keyboard = []


    # 2-Column Grid Layout


    if topics:


        grid_rows = []


        row = []


        for tid, name in topics:


            btn = InlineKeyboardButton(f"📑 {name}", callback_data=f"sel_top_{cat_id}_{tid}")


            row.append(btn)


            if len(row) == 2:


                grid_rows.append(row)


                row = []


        if row:


            grid_rows.append(row)


        keyboard.extend(grid_rows)


    nav_buttons = []


    if page > 0:


        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"sel_top_page_{cat_id}_{page-1}"))


    if offset + ITEMS_PER_PAGE < total_count:


        nav_buttons.append(InlineKeyboardButton("➡️ التالي", callback_data=f"sel_top_page_{cat_id}_{page+1}"))


    if nav_buttons:


        keyboard.append(nav_buttons)


    keyboard.append([InlineKeyboardButton("📂 فتاوى بدون موضوع", callback_data=f"sel_top_{cat_id}_none")])


    keyboard.append([InlineKeyboardButton("🔍 بحث في المواضيع", callback_data=f"search_topic_query_{cat_id}")])


    keyboard.append([InlineKeyboardButton("🔙 رجوع للتصنيفات", callback_data="search_category")]) # Or back logic


    msg = f"🏷️ التصنيف: **{safe_cat_name}**\n📑 **اختر الموضوع** (صفحة {page+1}):"


    reply_markup = InlineKeyboardMarkup(keyboard)


    if isinstance(update_obj, Update) and update_obj.callback_query:


        await update_obj.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


    elif hasattr(update_obj, 'edit_message_text'):


        await update_obj.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


    elif isinstance(update_obj, Update) and update_obj.message:


        await update_obj.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')


    return STATE_SEARCH_TOPIC


async def handle_topic_search_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):


    query = update.callback_query


    await query.answer()


    data = query.data


    if data.startswith("sel_top_page_"):


        parts = data.split('_')


        cat_id = int(parts[3])


        page = int(parts[4])


        return await show_topics_list(update, context, cat_id, page)


    elif data.startswith("search_topic_query_"):


        cat_id = int(data.split('_')[-1])


        # مسح ذاكرة البحث المؤقتة للموضوع


        context.user_data['search_topic_query'] = None


        await query.edit_message_text("🔍 أرسل اسم الموضوع للبحث عنه:", reply_markup=InlineKeyboardMarkup([


            [InlineKeyboardButton("🔙 إلغاء البحث", callback_data=f"sel_cat_{cat_id}")],


            [InlineKeyboardButton("\U0001f3e0 \u0627\u0644\u0642\u0627\u0626\u0645\u0629 \u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629", callback_data="back_main")]


        ]))


        return STATE_SEARCH_TOPIC_SEARCH


    elif data.startswith("sel_top_"):


        parts = data.split('_')


        cat_id = int(parts[2])


        topic_id = parts[3] # could be "none" or int


        if topic_id != "none":


            topic_id = int(topic_id)


        return await fetch_and_display_cat_fatwas(update, context, cat_id, topic_id)


    elif data == "search_category": # Back button handling manually


        await show_categories_list(update, context, 0)


        return STATE_SEARCH_CATEGORY


# ==================== عام (الأحدث / الأكثر مشاهدة) ====================


async def search_latest(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """أحدث الفتاوى"""


    query = update.callback_query


    # منع الضغط المتكرر على الزر أثناء تحميل نفس النتائج


    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):


        await query.answer("⏳ يتم بالفعل جلب أحدث الفتاوى...", show_alert=False)


        return


    await query.answer()


    # مسح ذاكرة البحث المؤقتة عند بدء بحث جديد


    context.user_data['search_cat_query'] = None


    context.user_data['search_topic_query'] = None


    context.user_data['scholar_search_query'] = None


    public_status = 'published' if not bot_db.is_admin(update.effective_user.id) else None


    context.user_data['current_search_state'] = {


        'type': 'latest',


        'params': {'public': public_status == 'published', 'cap': LATEST_LIMIT}


    }


    results, total_count = db.get_all_fatwas(status=public_status, limit=5, offset=0)


    if total_count > LATEST_LIMIT:


        total_count = LATEST_LIMIT

    context.user_data['current_search_state']['params']['cap'] = total_count


    await display_search_results(update, context, results, "📅 أحدث الفتاوى", total_count, is_callback=True, back_callback="back_main")


async def search_popular(update: Update, context: ContextTypes.DEFAULT_TYPE):


    """الأكثر مشاهدة"""


    query = update.callback_query


    # منع الضغط المتكرر على الزر أثناء تحميل نفس النتائج


    if callback_guard.is_fast_repeat(update.effective_user.id, query.data):


        await query.answer("⏳ يتم بالفعل جلب الأكثر مشاهدة...", show_alert=False)


        return


    await query.answer()


    public_only = not bot_db.is_admin(update.effective_user.id)


    # Store State


    context.user_data['current_search_state'] = {


        'type': 'popular',


        'params': {'public': public_only, 'cap': POPULAR_LIMIT}


    }


    results, total_count = _fetch_popular_fatwas(public_only, limit=5, offset=0, max_total=POPULAR_LIMIT)

    context.user_data['current_search_state']['params']['cap'] = total_count


    await display_search_results(update, context, results, "🔥 الأكثر مشاهدة", total_count, is_callback=True, back_callback="back_main")


# ==================== أدوات العرض ====================


async def display_search_results(update, context, results, title, total_count, is_callback=False, page=0, back_callback="search_fatwas", back_label=None):


    """عرض قائمة النتائج مع تفحيم (Server-Side Pagination)"""


    from core.utils import split_long_message, format_fatwa_card


    ITEMS_PER_PAGE = 5  # Must match the DB limit


    total_pages = (total_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE


    back_label = back_label or ("🏠 القائمة الرئيسية" if back_callback == "back_main" else "🔙 رجوع")


    if not results and page == 0:


        text = f"❌ لا توجد نتائج لـ: {title}"


        keyboard = [[InlineKeyboardButton(back_label, callback_data=back_callback)]]


        reply_markup = InlineKeyboardMarkup(keyboard)


        if is_callback:


            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)


        else:


            await update.message.reply_text(text, reply_markup=reply_markup)


        return


    # Prepare text (escape for Markdown)


    safe_title = escape_markdown(title) if title else title


    text = f"📚 **{safe_title}**\n"


    text += f"──────────────\n"


    text += f"نتائج {page * ITEMS_PER_PAGE + 1}-{min((page + 1) * ITEMS_PER_PAGE, total_count)} من {total_count}\n\n"


    for fatwa in results:


        text += format_fatwa_card(fatwa, use_markdown=True) + "\n\n"


    # Buttons for Fatwas
    fatwa_buttons = []
    current_view_row = []
    
    for fatwa in results:
        fid = fatwa['id']
        num = fatwa.get('fatwa_number', fid)
        btn = InlineKeyboardButton(f"📖 عرض الفتوى #{num}", callback_data=f"view_{fid}_search")

        current_view_row.append(btn)
        if len(current_view_row) == 2:
            fatwa_buttons.append(current_view_row)
            current_view_row = []
    
    if current_view_row:
        fatwa_buttons.append(current_view_row)

    # Pagination & Navigation
    back_btn = InlineKeyboardButton(back_label, callback_data=back_callback)

    reply_markup = create_pagination_keyboard(
        current_page=page,
        total_pages=total_pages,
        callback_prefix="res_page",
        back_button=back_btn,
        extra_buttons=fatwa_buttons
    )


    # Save State for context reconstruction if needed (mostly for title/back consistency)


    context.user_data['last_search_context'] = {


        'title': title,


        'back_callback': back_callback,
        'back_label': back_label


    }


    # FIX: Save that we have active search results so view_fatwa back button works
    context.user_data['last_search_results'] = True
    context.user_data['last_search_page'] = page

    # Display logic


    message_parts = split_long_message(text)


    if is_callback:


        await update.callback_query.edit_message_text(


            message_parts[0],


            reply_markup=reply_markup if len(message_parts) == 1 else None,


            parse_mode='Markdown'


        )


    else:


        await update.message.reply_text(


            message_parts[0],


            reply_markup=reply_markup if len(message_parts) == 1 else None,


            parse_mode='Markdown'


        )


    # Send remaining parts if any


    if len(message_parts) > 1:


        for i, part in enumerate(message_parts[1:], 1):


            is_last = (i == len(message_parts) - 1)


            target = update.callback_query.message if is_callback else update.message


            await target.reply_text(


                part,


                reply_markup=reply_markup if is_last else None,


                parse_mode='Markdown'


            )


async def handle_search_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE):


    query = update.callback_query


    if callback_guard.is_fast_repeat(update.effective_user.id, "search_pagination"):


        await query.answer("⏳ ...", show_alert=False)


        return


    await query.answer()


    try:


        page = int(query.data.split('_')[-1])


    except (TypeError, ValueError, AttributeError, IndexError):


        page = 0


    search_state = context.user_data.get('current_search_state')


    if not search_state:


        await query.edit_message_text("⚠️ انتهت جلسة البحث. يرجى البحث مجدداً.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بحث جديد", callback_data="search_fatwas")]]))


        return


    # Re-execute query based on state


    stype = search_state.get('type')


    params = search_state.get('params', {})


    limit = 5


    offset = page * limit


    results = []


    total_count = 0


    # Recover helper context


    last_ctx = context.user_data.get('last_search_context', {})


    title = last_ctx.get('title', "نتائج البحث")


    back_callback = last_ctx.get('back_callback', "search_fatwas")
    back_label = last_ctx.get('back_label')


    # DB Fetch logic


    if stype == 'general':
        results, total_count = _fetch_general_text_fatwas(
            query_text=params['query'],
            public_only=params['public'],
            limit=limit,
            offset=offset,
            strict=bool(params.get('strict', False)),
            max_total=params.get('cap')
        )


    elif stype == 'title':


        results, total_count = db.search_fatwas(query_text=params['query'], public_only=params['public'], scope='title', limit=limit, offset=offset)


    elif stype == 'scholar':


        results, total_count = db.get_fatwas_by_scholar(params['scholar'], public_only=params['public'], limit=limit, offset=offset)


    elif stype == 'source':


        results, total_count = db.get_fatwas_by_source(params['id'], public_only=params['public'], limit=limit, offset=offset)


    elif stype == 'category_custom':


        # Replicate fetch_and_display_cat_fatwas logic


        cat_id = params.get('cat_id')


        topic_id = params.get('topic_id')


        public_sql = " AND f.status = 'published'" if params['public'] else ""


        conn = db.get_connection()


        c = conn.cursor()


        join_clause = ""


        where_clause = ""


        sql_params = []


        if topic_id == "none":


            join_clause = "JOIN fatwa_categories fc ON f.id = fc.fatwa_id"


            where_clause = f"""


                WHERE fc.category_id = ? {public_sql}


                  AND NOT EXISTS (


                    SELECT 1


                    FROM fatwa_topics ft


                    JOIN topics t ON ft.topic_id = t.id


                    WHERE ft.fatwa_id = f.id AND t.category_id = ?


                  )


            """


            sql_params = [cat_id, cat_id]


        elif topic_id:


            join_clause = "JOIN fatwa_topics ft ON f.id = ft.fatwa_id"


            where_clause = f"WHERE ft.topic_id = ? {public_sql}"


            sql_params = [topic_id]


        else:


            join_clause = "JOIN fatwa_categories fc ON f.id = fc.fatwa_id"


            where_clause = f"WHERE fc.category_id = ? {public_sql}"


            sql_params = [cat_id]


        # Count


        c.execute(f"SELECT COUNT(DISTINCT f.id) FROM fatwas f {join_clause} {where_clause}", sql_params)


        total_count = c.fetchone()[0]


        # Data


        sql = f"""


            SELECT f.id


            FROM fatwas f


            {join_clause}


            {where_clause}


            GROUP BY f.id


            ORDER BY f.fatwa_number DESC LIMIT ? OFFSET ?


        """


        sql_params.extend([limit, offset])


        c.execute(sql, sql_params)


        results = []


        for row in c.fetchall():


            fatwa_data = db.get_fatwa(row[0])


            if fatwa_data:


                results.append(fatwa_data)


        conn.close()


    elif stype == 'category': # Default fallback incase legacy state


        results, total_count = db.search_fatwas(category_id=params.get('cat_id'), topic_id=params.get('topic_id'), public_only=params['public'], limit=limit, offset=offset)


    elif stype == 'latest':


         cap = params.get('cap')


         if cap is not None and offset >= cap:


             results, total_count = [], cap


         else:


             effective_limit = min(limit, cap - offset) if cap is not None else limit


             if effective_limit <= 0:


                 results, total_count = [], cap or 0


             else:


                 results, total_count = db.get_all_fatwas(


                     status='published' if params['public'] else None,


                     limit=effective_limit,


                     offset=offset


                 )


                 if cap is not None and total_count > cap:


                     total_count = cap


    elif stype == 'smart':
         results, total_count = _fetch_smart_fatwas(
             params.get('query'),
             params.get('use_title'),
             params.get('use_text'),
             params.get('scholars', []),
             params.get('public', True),
             limit,
             offset
         )
    elif stype == 'ai':
         results, total_count, debug_data = _fetch_ai_text_fatwas(
             query_terms=params.get('terms') or [],
             user_query=params.get('user_query'),
             public_only=params.get('public', True),
             limit=limit,
             offset=offset,
             max_total=params.get('cap'),
             include_debug=True,
         )
    elif stype == 'popular':


         results, total_count = _fetch_popular_fatwas(params['public'], limit=limit, offset=offset, max_total=params.get('cap'))


    await display_search_results(
        update,
        context,
        results,
        title,
        total_count,
        is_callback=True,
        page=page,
        back_callback=back_callback,
        back_label=back_label
    )


# ==================== تعريف المحادثة ====================


from handlers.general import cancel_operation, back_to_main


async def view_fatwa_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):


    from handlers.fatwa import view_fatwa


    return await view_fatwa(update, context)


async def show_random_fatwa_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):


    from handlers.fatwa import show_random_fatwa


    return await show_random_fatwa(update, context)


async def continue_reading_fatwa_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):


    from handlers.fatwa import continue_reading_fatwa


    return await continue_reading_fatwa(update, context)


search_conv = ConversationHandler(


    entry_points=[
        CallbackQueryHandler(start_search, pattern='^search_fatwas$'),
        CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),
        CallbackQueryHandler(start_smart_search, pattern='^search_smart$'),
        CallbackQueryHandler(search_ai_prompt, pattern='^search_ai$')
    ],


    states={


        STATE_SEARCH: [


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'), # Retry/Refresh


            CallbackQueryHandler(start_smart_search, pattern='^search_smart$'),


            CallbackQueryHandler(search_ai_prompt, pattern='^search_ai$'),

            CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),

            CallbackQueryHandler(show_random_fatwa_proxy, pattern=r'^random_fatwa(?:_\d+)?$'),

            CallbackQueryHandler(continue_reading_fatwa_proxy, pattern=r'^continue_read_\d+(?:_.+)?$'),


            CallbackQueryHandler(search_number_prompt, pattern='^search_number$'),


            CallbackQueryHandler(search_title_prompt, pattern='^search_title$'),


            CallbackQueryHandler(search_all_prompt, pattern='^search_all$'),


            CallbackQueryHandler(search_scholar, pattern='^search_scholar$'),


            CallbackQueryHandler(search_category, pattern='^search_category$'),


            CallbackQueryHandler(search_source, pattern='^search_source$'),


            CallbackQueryHandler(back_to_main, pattern='^back_main$') # Exit


        ],


        STATE_SEARCH_AI: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_ai_query),


            CallbackQueryHandler(search_ai_prompt, pattern='^search_ai$'),


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),


            CallbackQueryHandler(cancel_operation, pattern='^cancel$'),


            CallbackQueryHandler(back_to_main, pattern='^back_main$')


        ],


        STATE_SMART_SEARCH: [


            CallbackQueryHandler(start_smart_search, pattern='^search_smart$'),


            CallbackQueryHandler(smart_toggle_title, pattern='^smart_toggle_title$'),


            CallbackQueryHandler(smart_toggle_text, pattern='^smart_toggle_text$'),


            CallbackQueryHandler(smart_open_scholars, pattern='^smart_select_scholar$'),


            CallbackQueryHandler(smart_ai_placeholder, pattern='^smart_ai$'),


            CallbackQueryHandler(smart_search_now, pattern='^smart_search_now$'),


            CallbackQueryHandler(smart_cancel, pattern='^smart_cancel$'),


            CallbackQueryHandler(back_to_main, pattern='^back_main$')


        ],


        STATE_SMART_SEARCH_SCHOLARS: [


            CallbackQueryHandler(handle_smart_scholar_selection, pattern='^smart_sch_')


        ],


        STATE_SMART_SEARCH_QUERY: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_smart_search_query),


            CallbackQueryHandler(start_smart_search, pattern='^search_smart$')


        ],


        STATE_SEARCH_TITLE: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_title),


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),


            CallbackQueryHandler(cancel_operation, pattern='^cancel$')


        ],


        STATE_SEARCH_ALL: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_all),


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),


            CallbackQueryHandler(cancel_operation, pattern='^cancel$')


        ],


        STATE_SEARCH_NUMBER: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, perform_search_number),


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),


            CallbackQueryHandler(cancel_operation, pattern='^cancel$')


        ],


        STATE_SEARCH_SCHOLAR: [


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'), # Back loop fix: Match specific first
            CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),


            CallbackQueryHandler(handle_search_pagination, pattern='^res_page_'),


            CallbackQueryHandler(view_fatwa_proxy, pattern='^view_'),

            CallbackQueryHandler(continue_reading_fatwa_proxy, pattern=r'^continue_read_\d+(?:_.+)?$'),

            CallbackQueryHandler(handle_scholar_selection) # Then catch-all


        ],


        STATE_SEARCH_CATEGORY: [


            CallbackQueryHandler(start_search, pattern='^search_fatwas$'),


            CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),


            CallbackQueryHandler(search_category, pattern='^search_category$'),


            CallbackQueryHandler(back_to_main, pattern='^back_main$'),


            CallbackQueryHandler(handle_category_search_selection, pattern='^search_cat_type_'),


            CallbackQueryHandler(handle_category_search_selection, pattern='^cat_page_'),


            CallbackQueryHandler(handle_category_search_selection, pattern='^sel_cat_'),


            CallbackQueryHandler(handle_category_search_selection, pattern='^search_cat_query$'),


            CallbackQueryHandler(handle_search_pagination, pattern='^res_page_'),


            CallbackQueryHandler(view_fatwa_proxy, pattern='^view_'),


            CallbackQueryHandler(continue_reading_fatwa_proxy, pattern=r'^continue_read_\d+(?:_.+)?$')


        ],


        STATE_SEARCH_TOPIC: [


            CallbackQueryHandler(handle_topic_search_selection),


            CallbackQueryHandler(search_category, pattern='^search_category$') # Back to cats


        ],


        STATE_SEARCH_SOURCE: [


            CallbackQueryHandler(search_source, pattern='^search_source$'),
            CallbackQueryHandler(start_browse_fatwas, pattern='^browse_fatwas$'),


            CallbackQueryHandler(handle_source_selection, pattern='^src_page_'),


            CallbackQueryHandler(handle_source_selection, pattern='^sel_source_'),


            CallbackQueryHandler(handle_source_selection, pattern='^search_source_query$'),


            CallbackQueryHandler(handle_source_selection, pattern='^clear_source_search$'),


            CallbackQueryHandler(handle_search_pagination, pattern='^res_page_'),


            CallbackQueryHandler(view_fatwa_proxy, pattern='^view_'),


            CallbackQueryHandler(continue_reading_fatwa_proxy, pattern=r'^continue_read_\d+(?:_.+)?$')


        ],


        STATE_SEARCH_SOURCE_QUERY: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_source_query),


            CallbackQueryHandler(search_source, pattern='^search_source$'),


            CallbackQueryHandler(back_to_main, pattern='^back_main$')


        ],


        STATE_SEARCH_CAT_SEARCH: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_cat_query),


            CallbackQueryHandler(search_category, pattern='^search_category$'),


            CallbackQueryHandler(back_to_main, pattern='^back_main$')


        ],


        STATE_SEARCH_TOPIC_SEARCH: [


            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_topic_query),


            CallbackQueryHandler(handle_category_search_selection, pattern='^sel_cat_'), # Use Category Selection handler


            CallbackQueryHandler(back_to_main, pattern='^back_main$')


        ],


    },


    fallbacks=[CallbackQueryHandler(start_search, pattern='^search_fatwas$'), CallbackQueryHandler(cancel_operation, pattern='^cancel$'), CommandHandler('cancel', cancel_operation)]


)
