import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from core.bot_db import BotDatabaseManager
from core.config import BOT_DB_NAME, FATWAS_DB_NAME, TELEGRAM_TOKEN
from core.database import FatwaDatabaseManager
from core.utils import normalize_text

# -----------------------------------------------------------
# App + CORS
# -----------------------------------------------------------

app = FastAPI(title="Fatwa CMS API", version="1.0.0")
_origins_raw = os.getenv("API_ALLOW_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",") if o.strip()]
ALLOW_CREDENTIALS = "*" not in ALLOWED_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------
# Database Managers
# -----------------------------------------------------------

fatwa_db = FatwaDatabaseManager(FATWAS_DB_NAME)
bot_db = BotDatabaseManager(BOT_DB_NAME)


# -----------------------------------------------------------
# Auth Helpers
# -----------------------------------------------------------

API_SECRET = os.getenv("API_SECRET") or TELEGRAM_TOKEN
if not API_SECRET:
    raise RuntimeError("API secret is missing. Set API_SECRET or TELEGRAM_TOKEN.")
TOKEN_TTL_SECONDS = int(os.getenv("API_TOKEN_TTL", "604800"))  # 7 days
MAX_PAGE = 1000
MAX_SEARCH_WINDOW = 5000


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def create_token(payload: Dict[str, Any]) -> str:
    body = payload.copy()
    body["exp"] = int(time.time()) + TOKEN_TTL_SECONDS
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sig = hmac.new(API_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64encode(raw)}.{_b64encode(sig)}"


def decode_token(token: str) -> Dict[str, Any]:
    try:
        body_b64, sig_b64 = token.split(".")
        raw = _b64decode(body_b64)
        expected = hmac.new(API_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64decode(sig_b64)):
            raise ValueError("Invalid signature")
        payload = json.loads(raw.decode("utf-8"))
        exp = int(payload.get("exp", 0))
        if exp and exp < int(time.time()):
            raise ValueError("Token expired")
        return payload
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


def _build_authenticated_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc

    return {
        "user_id": user_id,
        "username": payload.get("username"),
        "full_name": payload.get("full_name"),
        # Always resolve from DB to avoid stale/forged role in token claims.
        "is_admin": bot_db.is_admin(user_id),
    }


def get_current_user(request: Request) -> Dict[str, Any]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = auth.split(" ", 1)[1].strip()
    payload = decode_token(token)
    return _build_authenticated_user(payload)


def get_optional_user(request: Request) -> Optional[Dict[str, Any]]:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
        return _build_authenticated_user(payload)
    except HTTPException:
        return None


def verify_telegram_login(data: Dict[str, Any]) -> Dict[str, Any]:
    if not TELEGRAM_TOKEN:
        raise HTTPException(status_code=500, detail="Telegram token not configured")

    if "hash" not in data:
        raise HTTPException(status_code=400, detail="Missing hash")

    auth_date = int(data.get("auth_date", 0))
    if not auth_date:
        raise HTTPException(status_code=400, detail="Missing auth_date")

    # Allow 1 day window
    if int(time.time()) - auth_date > 86400:
        raise HTTPException(status_code=401, detail="Auth data expired")

    data_check = "\n".join(
        f"{k}={data[k]}" for k in sorted(data.keys()) if k != "hash"
    )
    secret_key = hashlib.sha256(TELEGRAM_TOKEN.encode("utf-8")).digest()
    computed_hash = hmac.new(
        secret_key, data_check.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed_hash, data["hash"]):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth")

    return data


# -----------------------------------------------------------
# Schemas
# -----------------------------------------------------------


class TelegramAuthPayload(BaseModel):
    data: Dict[str, Any]


class FatwaIdsPayload(BaseModel):
    ids: List[int] = Field(default_factory=list)


class FatwaCreatePayload(BaseModel):
    title: str
    question: str
    answer: str
    scholar_name: str
    source_name: str
    source_title: str = ""
    source_url: Optional[str] = None
    audio_url: Optional[str] = None
    status: Optional[str] = "published"
    classifications: List[Dict[str, Any]] = Field(default_factory=list)


class FatwaUpdatePayload(BaseModel):
    title: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    status: Optional[str] = None
    fatwa_number: Optional[int] = None
    scholar_name: Optional[str] = None
    source_name: Optional[str] = None
    source_title: Optional[str] = None
    source_url: Optional[str] = None
    audio_url: Optional[str] = None
    classifications: Optional[List[Dict[str, Any]]] = None


# -----------------------------------------------------------
# Formatting Helpers
# -----------------------------------------------------------


def _fatwa_to_api(fatwa: Dict[str, Any]) -> Dict[str, Any]:
    classifications = fatwa.get("classifications") or []
    categories = []
    topics = []
    for cls in classifications:
        name = cls.get("category_name")
        if name:
            categories.append(name)
        topics.extend(cls.get("topic_names") or [])

    # Deduplicate while preserving order
    def _dedupe(items: List[str]) -> List[str]:
        seen = set()
        out = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    return {
        "id": fatwa.get("id"),
        "fatwa_number": fatwa.get("fatwa_number"),
        "title": fatwa.get("title") or "",
        "question": fatwa.get("question") or "",
        "answer": fatwa.get("answer") or "",
        "status": fatwa.get("status") or "published",
        "views": fatwa.get("views") or 0,
        "favorites_count": fatwa.get("favorites_count") or 0,
        "scholar_name": fatwa.get("scholar_name") or "",
        "source_title": fatwa.get("source_title") or "",
        "source_name": fatwa.get("source_name") or "",
        "source_link": fatwa.get("source_url"),
        "audio_link": fatwa.get("audio_url"),
        "categories": _dedupe(categories),
        "topics": _dedupe([t for t in topics if t]),
        "classifications": classifications,
    }


def _update_fatwa_views(fatwa_id: int, new_views: int) -> None:
    conn = fatwa_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE fatwas SET views = ? WHERE id = ?", (new_views, fatwa_id))
        conn.commit()
    finally:
        conn.close()


def _update_favorites_count(fatwa_id: int, count: int) -> None:
    conn = fatwa_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE fatwas SET favorites_count = ? WHERE id = ?",
            (count, fatwa_id),
        )
        conn.commit()
    finally:
        conn.close()


def _normalize(val: Optional[str]) -> str:
    return normalize_text(val or "")


def _split_csv(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _extract_category_names(fatwa: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for cls in fatwa.get("classifications") or []:
        name = cls.get("category_name")
        if name:
            names.append(name)
    return names


def _matches_text_filter(value: Optional[str], needle_norm: str, exact: bool) -> bool:
    value_norm = _normalize(value)
    return value_norm == needle_norm if exact else needle_norm in value_norm


def _matches_filters(
    fatwa: Dict[str, Any],
    scholar: Optional[str],
    scholars_norm: List[str],
    category: Optional[str],
    source: Optional[str],
    exact_scholar: bool,
    exact_category: bool,
    exact_source: bool,
) -> bool:
    scholar_name = fatwa.get("scholar_name")
    if scholars_norm:
        scholar_norm = _normalize(scholar_name)
        if exact_scholar:
            if scholar_norm not in scholars_norm:
                return False
        else:
            if not any(s in scholar_norm for s in scholars_norm):
                return False
    elif scholar:
        scholar_norm = _normalize(scholar)
        if not _matches_text_filter(scholar_name, scholar_norm, exact_scholar):
            return False

    if category:
        category_norm = _normalize(category)
        category_names = _extract_category_names(fatwa)
        if exact_category:
            if not any(_normalize(name) == category_norm for name in category_names):
                return False
        else:
            if not any(category_norm in _normalize(name) for name in category_names):
                return False

    if source:
        source_norm = _normalize(source)
        source_values = [fatwa.get("source_name"), fatwa.get("source_title")]
        if exact_source:
            if not any(_normalize(val) == source_norm for val in source_values):
                return False
        else:
            if not any(source_norm in _normalize(val) for val in source_values):
                return False

    return True


# -----------------------------------------------------------
# Auth Endpoints
# -----------------------------------------------------------


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/telegram")
def login_telegram(payload: TelegramAuthPayload) -> Dict[str, Any]:
    data = verify_telegram_login(payload.data)

    user_id = int(data["id"])
    username = data.get("username")
    first_name = data.get("first_name") or ""
    last_name = data.get("last_name") or ""
    full_name = (first_name + " " + last_name).strip()

    bot_db.add_user(user_id, username=username, full_name=full_name)
    is_admin = bot_db.is_admin(user_id)

    token = create_token(
        {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
        }
    )

    return {
        "token": token,
        "user": {
            "id": user_id,
            "username": username,
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "photo_url": data.get("photo_url"),
            "auth_date": data.get("auth_date"),
        },
        "is_admin": is_admin,
    }


@app.get("/api/me")
def me(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    return {"user": user}


# -----------------------------------------------------------
# Fatwa Endpoints
# -----------------------------------------------------------


@app.get("/api/fatwas")
def list_fatwas(
    query: Optional[str] = None,
    scholar: Optional[str] = None,
    scholars: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    fatwa_number: Optional[str] = None,
    exact_scholar: bool = False,
    exact_category: bool = False,
    exact_source: bool = False,
    sort: str = "latest",
    scope: str = "all",
    fields: str = "title,text",
    status: str = "published",
    page: int = 1,
    page_size: int = 20,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> Dict[str, Any]:
    page = min(max(1, page), MAX_PAGE)
    page_size = min(max(1, page_size), 200)
    if sort not in {"latest", "views"}:
        raise HTTPException(status_code=400, detail="Invalid sort")

    is_admin = bool(user and user.get("is_admin"))
    if status not in {"published", "draft", "all"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    if status != "published" and not is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    scholars_norm = [_normalize(s) for s in _split_csv(scholars) if _normalize(s)]

    # Direct lookup by number
    if fatwa_number:
        try:
            num = int(str(fatwa_number).strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid fatwa_number")
        fatwa = fatwa_db.get_fatwa_by_number(num)
        if not fatwa:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        if status == "draft" and fatwa.get("status") != "draft":
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        if status == "published" and fatwa.get("status") != "published":
            if not is_admin:
                return {"items": [], "total": 0, "page": page, "page_size": page_size}
        if not _matches_filters(
            fatwa=fatwa,
            scholar=scholar,
            scholars_norm=scholars_norm,
            category=category,
            source=source,
            exact_scholar=exact_scholar,
            exact_category=exact_category,
            exact_source=exact_source,
        ):
            return {"items": [], "total": 0, "page": page, "page_size": page_size}
        return {
            "items": [_fatwa_to_api(fatwa)],
            "total": 1,
            "page": 1,
            "page_size": 1,
        }

    # Resolve category by name (best-effort)
    category_id = None
    if category:
        normalized = _normalize(category)
        for cid, name in fatwa_db.get_categories(search_query=category):
            if _normalize(name) == normalized:
                category_id = cid
                break
        if exact_category and category_id is None:
            return {"items": [], "total": 0, "page": page, "page_size": page_size}

    public_only = not is_admin or status == "published"

    # Determine smart fields
    fields_set = {f.strip() for f in fields.split(",") if f.strip()}
    title_only = fields_set == {"title"}
    text_only = fields_set == {"text"}

    results: List[Dict[str, Any]] = []
    total_count = 0

    if not query and not scholar and not scholars_norm and not category and not source:
        if sort == "views":
            conn = fatwa_db.get_connection()
            try:
                cur = conn.cursor()
                where = ""
                params: List[Any] = []
                if status in {"published", "draft"}:
                    where = "WHERE status = ?"
                    params.append(status)
                cur.execute(f"SELECT COUNT(*) FROM fatwas {where}", params)
                total_count = cur.fetchone()[0]

                cur.execute(
                    f"""
                    SELECT id
                    FROM fatwas
                    {where}
                    ORDER BY COALESCE(views, 0) DESC, fatwa_number DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [page_size, (page - 1) * page_size],
                )
                ids = [row[0] for row in cur.fetchall()]
            finally:
                conn.close()

            for fatwa_id in ids:
                fatwa = fatwa_db.get_fatwa(fatwa_id)
                if fatwa:
                    results.append(fatwa)
        else:
            fatwas, total = fatwa_db.get_all_fatwas(
                status=None if status == "all" else status,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            results = fatwas
            total_count = total
    else:
        # Base search
        requires_wide_window = bool(
            source
            or scholars_norm
            or (category and category_id is None)
            or exact_scholar
            or exact_category
            or exact_source
        )
        search_window = (
            MAX_SEARCH_WINDOW
            if requires_wide_window
            else min(max(page_size * page, 100), MAX_SEARCH_WINDOW)
        )
        raw_results, _total = fatwa_db.search_fatwas(
            query_text=query,
            scholar=scholar if not scholars_norm else None,
            category_id=category_id,
            public_only=public_only,
            scope="title" if title_only else scope,
            limit=search_window,
            offset=0,
        )

        filtered = raw_results

        if text_only and query:
            q = _normalize(query)
            filtered = [
                f
                for f in filtered
                if q in _normalize(f.get("question"))
                or q in _normalize(f.get("answer"))
            ]

        filtered = [
            f
            for f in filtered
            if _matches_filters(
                fatwa=f,
                scholar=scholar,
                scholars_norm=scholars_norm,
                category=category,
                source=source,
                exact_scholar=exact_scholar,
                exact_category=exact_category,
                exact_source=exact_source,
            )
        ]

        if status == "draft":
            filtered = [f for f in filtered if f.get("status") == "draft"]
        elif status == "published":
            filtered = [f for f in filtered if f.get("status") == "published"]

        if sort == "views":
            filtered = sorted(filtered, key=lambda f: f.get("views", 0), reverse=True)

        total_count = len(filtered)
        start = (page - 1) * page_size
        end = start + page_size
        results = filtered[start:end]

    return {
        "items": [_fatwa_to_api(f) for f in results],
        "total": total_count,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/fatwas/{fatwa_id}")
def get_fatwa(
    fatwa_id: int,
    increment: int = 1,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> Dict[str, Any]:
    fatwa = fatwa_db.get_fatwa(fatwa_id)
    if not fatwa:
        raise HTTPException(status_code=404, detail="Not found")

    if fatwa.get("status") != "published" and not (user and user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Draft")

    if increment:
        # Increment views
        new_views = (fatwa.get("views") or 0) + 1
        _update_fatwa_views(fatwa_id, new_views)
        fatwa = fatwa_db.get_fatwa(fatwa_id)

    return _fatwa_to_api(fatwa)


@app.get("/api/fatwas/{fatwa_id}/related")
def related_fatwas(
    fatwa_id: int,
    limit: int = 5,
    user: Optional[Dict[str, Any]] = Depends(get_optional_user),
) -> Dict[str, Any]:
    public_only = not (user and user.get("is_admin"))
    fatwas = fatwa_db.get_related_fatwas(fatwa_id, limit=limit, public_only=public_only)
    return {"items": [_fatwa_to_api(f) for f in fatwas]}


@app.get("/api/fatwas/random")
def random_fatwas(limit: int = 5) -> Dict[str, Any]:
    normalized_limit = max(1, min(limit, 10))
    items = []
    seen_ids = set()
    attempts = 0
    max_attempts = normalized_limit * 4

    while len(items) < normalized_limit and attempts < max_attempts:
        attempts += 1
        fatwa = fatwa_db.get_random_published_fatwa()
        if fatwa:
            fatwa_id = fatwa.get("id")
            if fatwa_id in seen_ids:
                continue
            seen_ids.add(fatwa_id)
            items.append(_fatwa_to_api(fatwa))
    return {"items": items}


@app.post("/api/fatwas/by-ids")
def fatwas_by_ids(payload: FatwaIdsPayload) -> Dict[str, Any]:
    fatwas = fatwa_db.get_fatwas_by_ids(payload.ids, public_only=True)
    return {"items": [_fatwa_to_api(f) for f in fatwas]}


# -----------------------------------------------------------
# Favorites Endpoints
# -----------------------------------------------------------


@app.get("/api/favorites")
def get_favorites(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    ids = bot_db.get_user_favorite_ids(user["user_id"])
    return {"ids": ids}


@app.post("/api/favorites/{fatwa_id}/toggle")
def toggle_favorite(
    fatwa_id: int, user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    is_favorite = bot_db.toggle_favorite(user["user_id"], fatwa_id)

    # Update favorites_count
    conn = bot_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM favorites WHERE fatwa_id = ?", (fatwa_id,))
        count = cur.fetchone()[0]
    finally:
        conn.close()
    _update_favorites_count(fatwa_id, count)

    return {"is_favorite": is_favorite}


# -----------------------------------------------------------
# Dictionary Endpoints
# -----------------------------------------------------------


@app.get("/api/scholars")
def list_scholars() -> Dict[str, Any]:
    scholars = [
        {"id": sid, "name": name} for sid, name in fatwa_db.get_scholars()
    ]
    return {"items": scholars}


@app.get("/api/categories")
def list_categories() -> Dict[str, Any]:
    categories = [
        {"id": cid, "name": name} for cid, name in fatwa_db.get_categories()
    ]
    return {"items": categories}


@app.get("/api/sources")
def list_sources() -> Dict[str, Any]:
    sources = [{"id": sid, "name": name} for sid, name in fatwa_db.get_sources()]
    return {"items": sources}


# -----------------------------------------------------------
# Statistics
# -----------------------------------------------------------


@app.get("/api/stats")
def stats(user: Optional[Dict[str, Any]] = Depends(get_optional_user)) -> Dict[str, Any]:
    fatwa_stats = fatwa_db.get_statistics()
    bot_stats = bot_db.get_statistics()
    # Extra counts
    conn = fatwa_db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM topics")
        topics_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM scholars")
        scholars_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM sources")
        sources_count = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM source_titles WHERE audio_url IS NOT NULL AND audio_url != ''"
        )
        audio_count = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(favorites_count), 0) FROM fatwas")
        favorites_count = cur.fetchone()[0]
    finally:
        conn.close()

    stats_data = {
        **fatwa_stats,
        **bot_stats,
        "topics": topics_count,
        "scholars": scholars_count,
        "sources": sources_count,
        "audio": audio_count,
        "favorites_count": favorites_count,
    }
    stats_data["is_admin"] = bool(user and user.get("is_admin"))
    return stats_data


# -----------------------------------------------------------
# Admin Endpoints (Minimal)
# -----------------------------------------------------------


def _require_admin(user: Dict[str, Any]) -> None:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")


@app.post("/api/admin/fatwas")
def create_fatwa(
    payload: FatwaCreatePayload, user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    _require_admin(user)
    fatwa_id = fatwa_db.add_fatwa(payload.dict())
    fatwa = fatwa_db.get_fatwa(fatwa_id)
    return {"item": _fatwa_to_api(fatwa)}


@app.put("/api/admin/fatwas/{fatwa_id}")
def update_fatwa(
    fatwa_id: int,
    payload: FatwaUpdatePayload,
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    _require_admin(user)
    success = fatwa_db.update_fatwa(fatwa_id, payload.dict(exclude_unset=True))
    if not success:
        raise HTTPException(status_code=404, detail="Not found")
    fatwa = fatwa_db.get_fatwa(fatwa_id)
    return {"item": _fatwa_to_api(fatwa)}


@app.delete("/api/admin/fatwas/{fatwa_id}")
def delete_fatwa(
    fatwa_id: int, user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    _require_admin(user)
    success = fatwa_db.delete_fatwa(fatwa_id)
    if not success:
        raise HTTPException(status_code=404, detail="Not found")
    return {"status": "success"}


@app.post("/api/admin/fatwas/suggest-tags")
async def suggest_tags(
    payload: Dict[str, str], user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, Any]:
    _require_admin(user)
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    # This will call the existing AI extraction logic from search.py 
    # but we need to import it or implement a similar one for tagging.
    # For now, let's implement a simple version here or call handlers.search.
    from handlers.search import _request_ai_query_terms
    
    queries, error = _request_ai_query_terms(text)
    if error:
        return {"items": [], "error": error}
    
    return {"suggestions": queries}
