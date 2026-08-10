"""OmaTasku RSS Mirror Service Main FastAPI Server Module.

Coordinates inbound RSS feed proxies, premium signed URL resolutions, parallel HEAD
Content-Length validations, Violentmonkey template distributions, and Prometheus tracking.
"""

# Approved Pylint Disables
# pylint: disable=missing-function-docstring,missing-class-docstring
# pylint: disable=too-many-locals,too-many-branches,line-too-long,broad-except

import argparse
import asyncio
from contextlib import asynccontextmanager
import mimetypes
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse
import uuid
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
import httpx
from pydantic import BaseModel, Field, field_validator
import uvicorn

# Load database and tracing helper modules
import database
import tracing

# Ensure .user.js files are served with the correct text/javascript content-type
# This is crucial for browser extensions like Violentmonkey or Tampermonkey to detect and install the script automatically!
mimetypes.add_type("text/javascript", ".user.js")

# Initialize settings
# Prioritize the BASE_URL environment variable (essential when running Uvicorn natively inside Docker)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
RSS_CACHE_TTL = int(os.getenv("RSS_CACHE_TTL", "60"))
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "00000000-0000-0000-0000-000000000000")
PIANO_TAC_COOKIE = os.getenv("PIANO_TAC_COOKIE")

# In-memory RSS feed cache
# Key: show_slug (str), Value: {"content": str, "expiry": float}
rss_cache = {}

# User-feed specific cache to avoid rewriting and resolving on every request
# Key: (user_id, show_slug), Value: {"content": str, "expiry": float}
user_feed_cache = {}

# In-memory http requests tracking for metrics
# Key: (method, path, status_code), Value: count (int)
http_metrics = {}

# In-memory outbound HEAD requests tracking for metrics
# Key: (show_slug, status_code), Value: count (int)
outbound_head_metrics = {}

# Register standard namespaces to preserve prefix names on XML serialization
NAMESPACES = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "atom": "http://www.w3.org/2005/Atom",
    "sy": "http://purl.org/rss/1.0/modules/syndication/",
    "slash": "http://purl.org/rss/1.0/modules/slash/",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "psc": "https://podlove.org/simple-chapters/",
    "podcast": "https://podcastindex.org/namespace/1.0",
    "spotify": "http://www.spotify.com/ns/rss"
}
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Handles startup and shutdown events for the FastAPI application."""
    # Startup DB initialization
    database.init_db()

    # Print startup logs (essential for native Uvicorn executions where __main__ is bypassed)
    print("Starting OmaTasku service...")
    print(f"Database Path: {database.DB_PATH}")
    print(f"Configured External Base URL: {BASE_URL}")

    # Auto-register default user mapping if provided in environmental variables
    if DEFAULT_USER_ID and PIANO_TAC_COOKIE:
        existing = database.get_user(DEFAULT_USER_ID)
        if not existing:
            # pylint: disable=broad-except
            try:
                database.create_user(
                    uuid=DEFAULT_USER_ID,
                    tac_cookie=PIANO_TAC_COOKIE,
                    comment="Default Server Owner"
                )
                print(f"OmaTasku: Successfully auto-registered default User ID {DEFAULT_USER_ID}")
            except Exception as e:
                print(f"OmaTasku Warning: Could not auto-register default user: {e}")
        else:
            # Optionally update default user's cookie if it has changed in the environment!
            if existing.get("tac_cookie") != PIANO_TAC_COOKIE:
                # pylint: disable=broad-except
                try:
                    database.update_user(
                        uuid=DEFAULT_USER_ID,
                        tac_cookie=PIANO_TAC_COOKIE,
                        comment="Default Server Owner"
                    )
                    log_msg = (
                        f"OmaTasku: Successfully updated default User ID {DEFAULT_USER_ID} "
                        "with new environment cookie."
                    )
                    print(log_msg)
                except Exception as e:
                    print(f"OmaTasku Warning: Could not update default user cookie: {e}")
    yield

app = FastAPI(
    title="OmaTasku RSS Mirror",
    description="Asynchronous FastAPI proxy service that mirrors premium podcast RSS feeds.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permits userscripts to push cookie updates from the news pages
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument the FastAPI app for OpenTelemetry OTLP tracing
tracing.instrument_fastapi_app(app)

@app.middleware("http")
async def track_http_requests(request: Request, call_next):
    response = await call_next(request)
    method = request.method

    # Check if the request successfully matched any registered route in FastAPI
    route = request.scope.get("route")
    if route:
        # Native FastAPI parameterized path template (e.g. "/{user_id}/postimees/rss/shows/{show_slug}")
        # This natively abstracts away individual user UUIDs, preventing cardinality explosion and data leaks!
        path = route.path
    else:
        # Group all unmatched requests (404s, WordPress/PHP scans, etc.) under a single label "*"
        path = "*"

    status_code = response.status_code

    metric_key = (method, path, status_code)
    http_metrics[metric_key] = http_metrics.get(metric_key, 0) + 1

    return response

# Security Validation and Sanitization Helpers
def clean_and_validate_tac(v: str) -> str:
    """Strictly validates the __tac cookie (must be a secure JWT Base64URL string)."""
    if not v:
        raise ValueError("täitmata (ei tohi olla tühi).")
    v_clean = v.strip()
    if len(v_clean) > 2000:
        raise ValueError("on liiga pikk (maksimaalselt 2000 tähemärki).")
    # Base64URL JWT format regex (only letters, digits, underscores, hyphens, and exactly two dots)
    jwt_pattern = r"^[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+$"
    if not re.match(jwt_pattern, v_clean):
        raise ValueError("vigane. Küpsis peab olema korrektne ja turvaline JWT-kujul seansitoken.")
    return v_clean

def clean_and_validate_comment(v: Optional[str]) -> Optional[str]:
    """Strictly sanitizes and restricts comment/username (allows letters/numbers across languages and safe punctuation)."""
    if v is None:
        return v
    v_clean = v.strip()
    if len(v_clean) > 100:
        raise ValueError("on liiga pikk (maksimaalselt 100 tähemärki).")
    # Allowed safe punctuation characters (spaces, periods, commas, hyphens, and underscores)
    allowed_punctuation = {" ", ".", ",", "-", "_"}
    # Verify there are no malicious scripting characters (like <, >, &, ", ', ;, \, etc.)
    if not all(c.isalnum() or c in allowed_punctuation for c in v_clean):
        raise ValueError("sisaldab keelatud erisümboleid. Lubatud on ainult tähed, numbrid, tühikud, punktid, komad, kriipsud ja alakriipsud.")
    return v_clean

# Pydantic Schemas for API with Strict Input Sanitization
class UserCreate(BaseModel):
    uuid: str = Field(..., description="Cryptographic unique ID (UUIDv4) for the user.")
    tac_cookie: str = Field(..., description="The __tac cookie retrieved from the browser.")
    comment: Optional[str] = Field(None, description="Optional username or description.")

    @field_validator("uuid")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError as exc:
            raise ValueError("vigane. Kordumatu kasutaja ID peab olema korrektses UUIDv4-kujus.") from exc
        return v

    @field_validator("tac_cookie")
    @classmethod
    def validate_tac_cookie(cls, v: str) -> str:
        return clean_and_validate_tac(v)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: Optional[str]) -> Optional[str]:
        return clean_and_validate_comment(v)

class UserUpdate(BaseModel):
    tac_cookie: str = Field(..., description="The __tac cookie retrieved from the browser.")
    comment: Optional[str] = Field(None, description="Optional username or description.")

    @field_validator("tac_cookie")
    @classmethod
    def validate_tac_cookie(cls, v: str) -> str:
        return clean_and_validate_tac(v)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: Optional[str]) -> Optional[str]:
        return clean_and_validate_comment(v)

# REST API Endpoints
@app.get("/api/uuid")
def generate_new_uuid():
    """Generates a fresh cryptographically secure random UUIDv4."""
    return {"uuid": str(uuid.uuid4())}

# In-memory global registration rate limit tracker (O(1) memory, resilient to leaks)
registration_limiter = {
    "registration_count": 0,
    "window_start": time.time()
}

def check_registration_rate_limit():
    """Enforces a strict global rate limit of max 2 new registrations per minute, protecting the DB from automated bulk registrations."""
    now = time.time()

    # If the 60-second window has passed, reset the counter
    if now - registration_limiter["window_start"] >= 60:
        registration_limiter["window_start"] = now
        registration_limiter["registration_count"] = 0

    # Check limit
    if registration_limiter["registration_count"] >= 2:
        raise HTTPException(
            status_code=429,
            detail="OmaTasku Security: Liiga palju päringuid. Uute seansside loomise ülemaailmne limiit on ületatud (maksimaalselt 2 uut seanssi minutis). Palun proovi hetke pärast uuesti."
        )

    # Increment counter
    registration_limiter["registration_count"] += 1

@app.post("/api/users", status_code=201)
def register_user(user_data: UserCreate):
    """Registers a brand-new user UUID mapping with their __tac cookie and metadata."""
    # Enforce strict global rate limiting (max 2 per minute) to prevent database bloat / DoS attacks
    check_registration_rate_limit()

    # Validate UUID format
    try:
        uuid.UUID(user_data.uuid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Vigane kasutaja ID vorming. UUIDv4 on kohustuslik.") from exc

    # Check if user already exists
    existing = database.get_user(user_data.uuid)
    if existing:
        raise HTTPException(status_code=400, detail="Kasutaja ID on juba registreeritud. Seadete uuendamiseks kasuta PUT päringut.")

    try:
        user = database.create_user(
            uuid=user_data.uuid,
            tac_cookie=user_data.tac_cookie,
            comment=user_data.comment
        )
        return user
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Andmebaasi viga: {str(e)}") from e

@app.get("/api/users/{user_id}")
def get_user_details(user_id: str):
    """Retrieves an existing user details, first registration, and update timestamps."""
    # Validate UUID path parameter format strictly to prevent bad input
    try:
        uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Vigane kasutaja ID vorming. UUIDv4 on kohustuslik.") from exc

    user = database.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Kasutaja ID-d ei leitud.")
    return user

@app.put("/api/users/{user_id}")
def update_user_session(user_id: str, user_data: UserUpdate):
    """Updates the __tac cookie and metadata for an existing User ID."""
    # Validate UUID path parameter format strictly to prevent bad input
    try:
        uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Vigane kasutaja ID vorming. UUIDv4 on kohustuslik.") from exc

    user = database.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Kasutaja ID-d ei leitud.")

    try:
        updated = database.update_user(
            uuid=user_id,
            tac_cookie=user_data.tac_cookie,
            comment=user_data.comment
        )
        return updated
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Andmebaasi viga: {str(e)}") from e

@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon_ico():
    """Serves the vector SVG favicon natively to prevent 404 browser errors."""
    return FileResponse("static/favicon.svg", media_type="image/svg+xml")

@app.get("/api/config")
def get_server_config():
    """Returns the configured base URL for RSS feed construction."""
    return {"base_url": BASE_URL}

@app.get("/metrics")
def serve_prometheus_metrics():
    """Serves standard plain-text metrics conforming to Prometheus specifications."""
    # Fetch database metrics
    session_count = database.get_session_count()
    last_reg_ts = database.get_last_registration_timestamp()

    lines = []

    # Sessions Total
    lines.append("# HELP omatasku_sessions_total Total number of active user sessions in the database.")
    lines.append("# TYPE omatasku_sessions_total gauge")
    lines.append(f"omatasku_sessions_total {session_count}")

    # Last Registration Timestamp
    lines.append("# HELP omatasku_last_registration_timestamp Last user registration epoch timestamp.")
    lines.append("# TYPE omatasku_last_registration_timestamp gauge")
    lines.append(f"omatasku_last_registration_timestamp {last_reg_ts}")

    # HTTP Requests Total
    lines.append("# HELP omatasku_http_requests_total Total number of HTTP requests processed.")
    lines.append("# TYPE omatasku_http_requests_total counter")

    # Export HTTP server metrics
    for (method, path, status), count in http_metrics.items():
        # Export labels conforming to Prometheus standards (method, path, status)
        lines.append(f'omatasku_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}')

    # Outbound HEAD Requests Total
    lines.append("# HELP omatasku_outbound_head_requests_total Total number of outbound premium MP3 file size HEAD requests.")
    lines.append("# TYPE omatasku_outbound_head_requests_total counter")

    # Export outbound HEAD metrics
    for (show_slug, status), count in outbound_head_metrics.items():
        lines.append(f'omatasku_outbound_head_requests_total{{show_slug="{show_slug}",status="{status}"}} {count}')

    # Standard trailing newline
    metrics_text = "\n".join(lines) + "\n"
    return HTMLResponse(content=metrics_text, media_type="text/plain")

async def fetch_rss_feed_cached(show_slug: str) -> str:
    """Fetches the target RSS feed asynchronously and caches it in memory."""
    now = time.time()

    # Check if cached and not expired
    if show_slug in rss_cache:
        entry = rss_cache[show_slug]
        if now < entry["expiry"]:
            return entry["content"]

    # Fetch from target platform (AS Postimees Grupp's AMS)
    target_url = f"https://ams.postimees.ee/rss/shows/{show_slug}"

    tracer = tracing.get_tracer()
    with tracer.start_as_current_span("fetch_original_rss") as span:
        span.set_attribute("omatasku.show_slug", show_slug)
        span.set_attribute("omatasku.target_url", target_url)

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(target_url)
                if response.status_code == 404:
                    raise HTTPException(status_code=404, detail=f"Podcast show '{show_slug}' not found on target platform.")
                response.raise_for_status()
                span.set_attribute("http.status_code", response.status_code)
            except httpx.HTTPStatusError as e:
                span.record_exception(e)
                raise HTTPException(status_code=502, detail=f"Bad Gateway: Target platform returned {e.response.status_code}") from e
            except httpx.RequestError as e:
                span.record_exception(e)
                raise HTTPException(status_code=502, detail=f"Bad Gateway: Request to target platform failed: {str(e)}") from e

        feed_content = response.text

    # Cache the result
    rss_cache[show_slug] = {
        "content": feed_content,
        "expiry": now + RSS_CACHE_TTL
    }
    return feed_content

# In-memory premium file size cache (never expires)
# Key: episode_id (str), Value: Content-Length (str)
file_size_cache = {}

def extract_episode_id(url: str) -> Optional[str]:
    """Helper to extract standard numeric episode IDs from CDN MP3 URLs."""
    if not url:
        return None
    match = re.search(r"(\d+)\.mp3", url)
    return match.group(1) if match else None

async def fetch_file_size(episode_id: str, premium_url: str, public_url: Optional[str] = None, show_slug: Optional[str] = None) -> Optional[str]:
    """Fetches the Content-Length of the premium MP3 file using a fast HEAD request, caching it permanently in SQLite using a clean public URL (stripped of random parameters) as key."""
    if not premium_url or not premium_url.startswith("http"):
        return None

    # Remove dynamic/random query parameters from the public URL to ensure the cache key remains stable
    clean_public_url = public_url.split("?")[0].strip() if public_url else None

    # 1. Check in-memory Cache (Layer 1)
    if episode_id in file_size_cache:
        return file_size_cache[episode_id]

    # 2. Check Database Cache (Layer 2) if clean_public_url is provided
    if clean_public_url:
        cached_db_size = database.get_file_size(clean_public_url)
        if cached_db_size:
            file_size_cache[episode_id] = cached_db_size
            return cached_db_size

    # 3. Cache Miss: Execute outbound network HEAD request
    tracer = tracing.get_tracer()
    with tracer.start_as_current_span("fetch_enclosure_size") as span:
        span.set_attribute("omatasku.episode_id", episode_id)
        span.set_attribute("omatasku.premium_url", premium_url)
        if clean_public_url:
            span.set_attribute("omatasku.public_url", clean_public_url)
        if show_slug:
            span.set_attribute("omatasku.show_slug", show_slug)

        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.head(premium_url, follow_redirects=True)
                status_code = response.status_code

                # Increment outbound head metrics grouped by show_slug and HTTP response status code
                if show_slug:
                    metric_key = (show_slug, status_code)
                    outbound_head_metrics[metric_key] = outbound_head_metrics.get(metric_key, 0) + 1

                if status_code == 200:
                    size = response.headers.get("Content-Length")
                    if size:
                        file_size_cache[episode_id] = size
                        span.set_attribute("omatasku.resolved_content_length", int(size))

                        # Save to SQLite database permanently using clean_public_url as key
                        if clean_public_url:
                            database.save_file_size(clean_public_url, size)

                        return size
            except Exception as e:
                # Increment outbound head metrics with "error" on network connection failures
                if show_slug:
                    metric_key = (show_slug, "error")
                    outbound_head_metrics[metric_key] = outbound_head_metrics.get(metric_key, 0) + 1

                span.record_exception(e)
                print(f"OmaTasku Warning: Could not resolve file size for {episode_id}: {e}")
    return None

async def resolve_premium_urls(episode_ids: list[str], tac_cookie: str) -> dict[str, str]:
    """Queries the premium URL proxy API asynchronously to batch resolve signatures."""
    if not episode_ids:
        return {}

    ids_param = ",".join(episode_ids)
    target_url = f"https://kuula.postimees.ee/api/proxy/ams/kuula/episodes/urls?ids={ids_param}"

    headers = {
        "Cookie": f"__tac={tac_cookie}",
        "User-Agent": "OmaTasku RSS Proxy/1.0"
    }

    tracer = tracing.get_tracer()
    with tracer.start_as_current_span("resolve_premium_urls") as span:
        span.set_attribute("omatasku.episode_ids_count", len(episode_ids))
        span.set_attribute("omatasku.episode_ids", str(episode_ids))

        # Censor the sensitive cookie from trace attributes!
        censored_cookie = tac_cookie[:10] + "..." if tac_cookie else "None"
        span.set_attribute("omatasku.tac_cookie_preview", censored_cookie)

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(target_url, headers=headers)
                response.raise_for_status()
                span.set_attribute("http.status_code", response.status_code)
                return response.json()
            except Exception as e:
                span.record_exception(e)
                print(f"OmaTasku Warning: Premium URL resolution failed: {e}")
                return {}

def rewrite_rss_xml(xml_text: str, premium_url_map: dict[str, str], premium_size_map: dict[str, str]) -> str:
    """Parses raw RSS feed XML and replaces preview enclosure URLs & sizes with premium ones."""
    if not premium_url_map:
        return xml_text

    try:
        root = ET.fromstring(xml_text)

        # Traverse items and find enclosure elements
        for item in root.findall(".//item"):
            enclosure = item.find("enclosure")
            if enclosure is not None:
                original_url = enclosure.get("url")
                episode_id = extract_episode_id(original_url)
                if episode_id and episode_id in premium_url_map:
                    premium_url = premium_url_map[episode_id]
                    enclosure.set("url", premium_url)
                    # Update Content-Length size if fetched
                    if episode_id in premium_size_map:
                        enclosure.set("length", str(premium_size_map[episode_id]))

        # Serialize to XML
        modified_xml = ET.tostring(root, encoding="utf-8").decode("utf-8")
        if not modified_xml.startswith("<?xml"):
            modified_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + modified_xml
        return modified_xml
    except Exception as e:
        print(f"OmaTasku Warning: XML rewriting failed, falling back to original: {e}")
        return xml_text

# Mirror routing for Option A
@app.get("/{user_id}/postimees/rss/shows/{show_slug}")
async def get_mirrored_rss(user_id: str, show_slug: str):
    """Fetches, resolves, caches, and returns the mirrored premium RSS feed."""
    now = time.time()

    # 1. Strict Input Sanitization & Format Checks
    # Validate UUID path parameter format strictly
    try:
        uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Vigane kasutaja ID vorming. UUIDv4 on kohustuslik.") from exc

    # Validate show_slug strictly to permit only safe characters (prevents SSRF & directory traversal)
    if not re.match(r"^[a-zA-Z0-9_\-]+$", show_slug):
        raise HTTPException(status_code=400, detail="Vigane saate tunnuse vorming. Lubatud on ainult tähed, numbrid, kriipsud ja alakriipsud.")

    # Check user registration
    user = database.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Kasutaja ID-d ei leitud või ei ole registreeritud.")

    # 2. Check user-feed cache
    cache_key = (user_id, show_slug)
    if cache_key in user_feed_cache:
        entry = user_feed_cache[cache_key]
        if now < entry["expiry"]:
            return HTMLResponse(content=entry["content"], media_type="application/rss+xml")

    # 3. Fetch original RSS feed content (using global cache)
    feed_xml = await fetch_rss_feed_cached(show_slug)

    # 4. Extract episode IDs and their corresponding original public media URLs
    episode_ids = []
    original_url_map = {}
    try:
        root = ET.fromstring(feed_xml)
        for item in root.findall(".//item"):
            enclosure = item.find("enclosure")
            if enclosure is not None:
                original_url = enclosure.get("url")
                episode_id = extract_episode_id(original_url)
                if episode_id:
                    episode_ids.append(episode_id)
                    original_url_map[episode_id] = original_url
    except Exception as e:
        print(f"OmaTasku Warning: XML parsing for ID collection failed: {e}")

    # 5. Batch resolve premium signed URLs (limit to 50 items for speed)
    premium_url_map = {}
    if episode_ids:
        premium_url_map = await resolve_premium_urls(episode_ids[:50], user["tac_cookie"])

    # 5b. Fetch premium file sizes concurrently in parallel (leveraging asyncio.gather)
    premium_size_map = {}
    if premium_url_map:
        # Filter out empty or non-HTTP URLs to prevent unnecessary warning prints or network requests
        valid_premium_urls = {ep_id: url for ep_id, url in premium_url_map.items() if url and url.startswith("http")}
        if valid_premium_urls:
            ep_ids = list(valid_premium_urls.keys())
            size_tasks = [fetch_file_size(ep_id, valid_premium_urls[ep_id], original_url_map.get(ep_id), show_slug) for ep_id in ep_ids]
            resolved_sizes = await asyncio.gather(*size_tasks)
            for ep_id, size in zip(ep_ids, resolved_sizes):
                if size:
                    premium_size_map[ep_id] = size

    # 6. Rewrite XML enclosures (URLs + sizes)
    rewritten_xml = rewrite_rss_xml(feed_xml, premium_url_map, premium_size_map)

    # 7. Store in user-feed cache
    user_feed_cache[cache_key] = {
        "content": rewritten_xml,
        "expiry": now + RSS_CACHE_TTL
    }

    return HTMLResponse(content=rewritten_xml, media_type="application/rss+xml")

# Userscript Serving Route with Custom Headers for Auto-Installation
@app.get("/static/omatasku.user.js")
def serve_user_script():
    """Serves the Violentmonkey userscript with custom headers that match Greasy Fork's Nginx configuration to trigger extension auto-installation."""
    script_path = os.path.join(os.path.dirname(__file__), "static", "omatasku.user.js")
    if not os.path.exists(script_path):
        raise HTTPException(status_code=404, detail="Userscript file not found.")

    try:
        with open(script_path, "r", encoding="utf-8") as f:
            script_content = f.read()

        # Dynamically template server URL and @connect hostname based on actual BASE_URL
        parsed_url = urlparse(BASE_URL)
        hostname = parsed_url.hostname or "localhost"

        templated_content = script_content.replace("http://localhost:8080", BASE_URL)
        templated_content = templated_content.replace("// @connect      localhost", f"// @connect      {hostname}")

        headers = {
            "Content-Disposition": "inline",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Content-Type-Options": "nosniff"
        }
        return Response(content=templated_content, media_type="text/javascript; charset=utf-8", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to serve templated userscript: {str(e)}") from e

# HTML UI route
@app.get("/", response_class=HTMLResponse)
def serve_home_page():
    """Serves the single-page HTML client interface."""
    static_file_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(static_file_path):
        return FileResponse(static_file_path)
    return HTMLResponse(content="<h1>static/index.html not found!</h1>", status_code=404)

# Mount static files (registered AFTER explicit routes so that /static/omatasku.user.js is matched first!)
app.mount("/static", StaticFiles(directory="static"), name="static")


# Main Entrypoint parsing arguments
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="OmaTasku - Asynchronous FastAPI Premium Podcast RSS Proxy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Interface to bind the server to."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on."
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="External Web URL of the service (e.g. 'https://podcast.mydomain.com/')."
    )
    parser.add_argument(
        "--db-dir",
        type=str,
        default=None,
        help="Directory to store the SQLite database file."
    )
    parser.add_argument(
        "--db-name",
        type=str,
        default=None,
        help="Name of the SQLite database file."
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Full path to the SQLite database file (overrides --db-dir and --db-name)."
    )
    parser.add_argument(
        "--rss-cache-ttl",
        type=int,
        default=None,
        help="Time to live in seconds for cached RSS feeds (default: 60)."
    )
    args = parser.parse_args()

    # Determine RSS Cache TTL
    if args.rss_cache_ttl is not None:
        RSS_CACHE_TTL = args.rss_cache_ttl

    # Determine Database Path
    if args.db_path:
        database.update_db_path(args.db_path)
    elif args.db_dir or args.db_name:
        db_dir = args.db_dir if args.db_dir else database.DB_DIR
        db_name = args.db_name if args.db_name else database.DB_NAME
        database.update_db_path(os.path.join(db_dir, db_name))

    # Determine Base URL
    env_base_url = os.getenv("BASE_URL")
    if args.base_url:
        BASE_URL = args.base_url.rstrip("/")
    elif env_base_url:
        BASE_URL = env_base_url.rstrip("/")
    else:
        # Default to localhost with port if port is not standard 80/443
        BASE_URL = f"http://localhost:{args.port}" if args.port != 80 else "http://localhost"

    print(f"Binding to host: {args.host} on port: {args.port}")

    uvicorn.run(app, host=args.host, port=args.port)
