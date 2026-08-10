# GEMINI.md - OmaTasku System Instructions (For AI Agents)

> **IMPORTANT NOTE:** This file (`GEMINI.md`) is strictly reserved as system instructions and context guidelines for **AI Agents** (such as Gemini CLI). For normal human user instructions, deployment guides, and developer setups, please refer to the main [README.md](./README.md) instead.

---

This file serves as the system instruction and context for the **OmaTasku** project. It contains domain research, technical specifications, and development guidelines for future reference.

---

## 1. Project Overview & Domain Research

**OmaTasku** (meaning "Own Podcast" in Estonian) is a lightweight Python-based FastAPI proxy service that mirrors official premium podcast RSS feeds. It intercepts public RSS feeds containing preview (teaser) episodes and swaps their signed audio URLs and file sizes with premium, fully-playable, and downloadable full-episode URLs and true file lengths, which are authorized using a valid subscriber session cookie.

### Domain Findings & API Mechanics

Through research on the target premium audio platform, we have mapped out the following infrastructure:

#### A. Authentication & Cookie Lifespan
- The platform uses the **Piano ID** (`piano.io`) subscription and identity platform.
- When a user logs in, a cookie named **`__tac`** (containing the Piano ID user token) is set.
- Passing this `__tac` cookie in the `Cookie` header of API requests enables access to subscribed, full-length audio tracks.
- **Cookie Lifespan:** A `__tac` cookie is long-lived (typically lasting up to 1 year, or until the user explicitly logs out of the platform in their browser), but it will eventually expire.
- **Static URLs:** To prevent users from having to update their podcast player RSS URLs when their `__tac` cookie expires, the system uses a **cryptographic User ID (UUID)** embedded in the RSS path. The user's current `__tac` cookie is mapped to their permanent User ID in a local database and can be updated anytime through a simple web dashboard without altering the podcast feed URL.

#### B. Native Show RSS Feeds
- Public podcast feeds are hosted by the platform provider.
- These feeds include `<enclosure>` tags pointing to standard teaser files on their CDN (`router.example.net`):
  `https://router.example.net/[hash]/preview/full/show-episodes/[id].mp3?c=8000&ddnt=[preview_signature]`

#### C. Audio URL Resolution API
- To play an episode, the frontend queries a proxy backend:
  `GET https://{target_host}/api/proxy/ams/kuula/episodes/urls?ids={episode_id}`
- **Response if Logged Out / Public:** Returns a CDN URL containing the `/preview/` path and a signature (`ddnt`) valid ONLY for the teaser snippet (usually ~74 seconds).
- **Response if Logged In / Subscribed:** (Requires forwarding the `__tac` cookie) Returns a CDN URL containing the `/full/` path and a signature (`ddnt`) valid for the **entire episode**:
  `https://router.example.net/[hash]/full/full/show-episodes/[id].mp3?c=8000&ddnt=[premium_signature]`
- **Signature Security:** The `ddnt` token is a cryptographic signature tied to the specific URL path. You cannot simply rewrite `/preview/` to `/full/` manually; the CDN will reject the request with `403 Forbidden`. However, once the premium URL is resolved via the API, it can be downloaded/played directly by any tool (like VLC, curl, or standard podcast players) without headers or cookies.

#### D. Enclosure File Size Correction
- The original RSS feed enclosure `length` attribute contains the preview file size (~3MB).
- Since full episodes are significantly larger (~100MB+), displaying the wrong size can confuse podcast players during streaming or downloading.
- **Resolution:** OmaTasku resolves the actual premium MP3 file size by executing fast asynchronous **`HEAD`** requests to the resolved CDN URLs and reading the **`Content-Length`** header (without downloading any audio payload). These requests are performed concurrently in parallel and the results are cached permanently to eliminate future network round-trips.

#### E. Observability & Monitoring
- Exposes a public-safe, high-performance **`/metrics`** endpoint designed for Prometheus scrapers.
- Incorporates a non-blocking request tracking HTTP middleware.
- **Card-Cardinality & Security:** Hides sensitive user UUIDs inside request paths dynamically using regex normalization, replacing them with a static `{user_id}` placeholder, preventing both data leaks and Prometheus label cardinality explosion.

---

## 2. System Architecture

The project is built on **Python 3.9+** using **FastAPI** to provide a fast, asynchronous proxy.

```
                    ┌─────────────────────────┐
                    │   Podcast Client App    │
                    └──────────┬──────────────┘
                               │
            1. Request RSS     │   5. Stream Premium MP3
  (/rss/{user_id}/{feed_slug}) │
                               ▼
  ┌────────────────────────────────────────────────────────────┐
  │ OmaTasku RSS Mirror Service (FastAPI)                      │
  │                                                            │
  │  ┌──────────────┐      2. Fetch RSS      ┌──────────┐      │
  │  │  Feed Proxy  ├───────────────────────>│  Public  │      │
  │  │  Controller  │<───────────────────────┤   RSS    │      │
  │  └──────┬───────┘      Original XML      └──────────┘      │
  │         │                                                  │
  │         │ 3. Resolve IDs & query                           │
  │         ▼                                                  │
  │  ┌──────────────┐   Get Full signed URLs   ┌────────┐      │
  │  │ URL Resolver ├─────────────────────────>│ Audio  │      │
  │  │              │<─────────────────────────┤ Backend│      │
  │  └──────┬───────┘      (With Cookie)       └────────┘      │
  │         │                                                  │
  │         ├─── (Query active __tac) ────┐                    │
  │         │                             │                    │
  │         ▼                             ▼                    │
  │  ┌──────────────┐             ┌──────────────┐             │
  │  │ Multi-Layer  │             │  SQLite /    │             │
  │  │ Cache System │             │  JSON Store  │             │
  │  └──────────────┘             └──────┬───────┘             │
  │                                      │                     │
  │         ┌────────────────────────────┘                     │
  │         ▼ 4. Update __tac mapping                          │
  │  ┌──────────────┐                                          │
  │  │ Web Update   │                                          │
  │  │ Portal (HTML)│<── [Web Browser (User updates cookie)]   │
  │  └──────────────┘                                          │
  └────────────────────────────────────────────────────────────┘
```

### Core Components

1. **Feed Proxy / Controller (`/{user_id}/postimees/rss/shows/{show_slug}`):**
   - Serves as the immutable feed endpoint.
   - Extracts `user_id` from the path and validates it against the database.
   - Retrieves the associated `__tac` cookie. If not found or invalid, falls back gracefully to public/teaser feeds.
   - Dynamically fetches the original RSS from the target provider.
   - Parses the XML, locates `<enclosure>` tags, extracts episode IDs, and bulk-resolves them to premium links using the `__tac` cookie.
   - Fires parallel, non-blocking `HEAD` requests to extract true `Content-Length` headers, overwrites enclosure URLs and sizes, and returns the modified XML.

2. **Multi-Layer Cache System:**
   - **Layer 1: Global original RSS XML cache (`rss_cache`)** - Caches the original platform feed XML globally for 60 seconds.
   - **Layer 2: User-specific premium rewritten XML cache (`user_feed_cache`)** - Caches the final, premium-rewritten RSS XML per user-show slug for 60 seconds (completely bypassing DB queries, parsing, and signatures resolution on consecutive fetches).
   - **Layer 3: Permanent file size cache (`file_size_cache`)** - Caches resolved premium MP3 file sizes permanently (since a published file's size never changes), executing zero future network calls for these items.

3. **Database Mapping Store (SQLite/JSON):**
   - Keeps a secure mapping of `user_id` (a cryptographic UUID) to the user's `__tac` cookie.
   - **Lifespan Startup Sync:** Automatically creates and registers the `DEFAULT_USER_ID` with the active environment's `PIANO_TAC_COOKIE` on startup, and automatically synchronizes them if changed.

4. **Web Update Portal (`/`):**
   - A simple, clean, mobile-friendly HTML webpage served by FastAPI.
   - **Custom UUID Restore (Disaster Recovery):** Allows users to type or paste their own custom/existing UUIDv4 during registration. This offers a seamless way to restore their identical accounts in case of database flushes, avoiding the need to change feed URLs inside their podcast player apps.
   - **State Machine UI:** Enables users to paste their cryptographic `user_id` and their updated `__tac` cookie, dynamically locking the UUID to read-only mode upon successful search retrieval or submission.
   - Displays copyable Option A RSS links for popular shows and provides a user-friendly setup guide.

5. **Observability Endpoint (`/metrics`):**
   - Serves Prometheus-compatible plain-text metrics.
   - **`omatasku_sessions_total`** (Gauge): Number of registered user sessions.
   - **`omatasku_last_registration_timestamp`** (Gauge): Epoch timestamp of the last session registration.
   - **`omatasku_http_requests_total`** (Counter): Access counts grouped by `method`, normalized `path` (replacing UUIDs with `{user_id}`), and HTTP response `status`.

---

## 3. Building and Running

### Prerequisites
- Python 3.9+
- `pip` or virtualenv for dependency management.

### Configuration (Strict 12-Factor App Environment)
Configure the service purely using environment variables (especially when running inside Docker in read-only mode):
```bash
# Optional default User ID & tac cookie on startup
DEFAULT_USER_ID="00000000-0000-0000-0000-000000000000"
PIANO_TAC_COOKIE="your_tac_cookie_value_here"

# Public External Base URL (used to generate RSS links in the UI)
BASE_URL="http://omatasku.example.com/"

# Cache TTL in seconds (default: 1 minute / 60 seconds)
RSS_CACHE_TTL=60

# Path to database directory and file (default: ./omatasku.db)
# Ideal for mounting a writeable volume at /data in read-only containers
DB_DIR="."
DB_NAME="omatasku.db"

# Native Uvicorn Socket Bindings (Uvicorn native environment configs)
UVICORN_PORT=8080
UVICORN_HOST=0.0.0.0
```

### Preferred Installation & Run Commands
Running OmaTasku natively via Uvicorn is the **strictly preferred and recommended production configuration**. It registers Uvicorn directly as the parent process (PID 1), which handles standard OS termination signals (`SIGTERM`, `SIGINT`) instantly and executes clean, graceful shut-downs of connection pools and Lifespan managers.

```bash
# 1. Initialize virtual environment
python3 -m venv .venv_sys
source .venv_sys/bin/activate

# 2. Install dependencies (FastAPI, Uvicorn, httpx, pytest)
pip install -r requirements.txt

# 3. Start development/production server natively with Uvicorn (RECOMMENDED)
UVICORN_PORT=8080 UVICORN_HOST=0.0.0.0 uvicorn main:app
```

#### Programmatic Fallback Option (Local CLI)
If you prefer running programmatically with traditional command-line arguments:
```bash
python3 main.py --host 0.0.0.0 --port 8080 --base-url "http://omatasku.example.com/"
```

---

## 4. Development Conventions

- **Asynchronous I/O:** Leverage Python's `asyncio` and `httpx.AsyncClient` for outbound HTTP. Execute concurrent sub-tasks using `asyncio.gather` (e.g. parallel HEAD requests) to maximize throughput.
- **Robust XML Parsing:** Use standard libraries (`xml.etree.ElementTree`) for clean, safe, and portable parsing of RSS feeds. Always register namespaces to preserve prefix labels.
- **Graceful Error Handling & Defensive Programming:**
  - Skip empty, blank, or invalid URLs in resolution and size mapping immediately.
  - If a user's `__tac` cookie is expired or invalid, log a warning and fall back to returning the public preview feed instead of crashing.
- **Testing:** Include testing scripts using `pytest` and `httpx.AsyncClient`'s transport mocks to verify parsing, proxy rewriting, and database mapping state.
