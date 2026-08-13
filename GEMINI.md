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
- **Card-Cardinality & Security:** Evaluates incoming path requests natively against FastAPI's internal router state. If matched, it uses the static parameterized route path (e.g. `"/{user_id}/postimees/rss/shows/{show_slug}"`), completely abstracting away individual user UUIDs. If un-matched (WordPress scanner probes, etc.), it groups them safely under a single label **`"*"`**, preventing Prometheus label cardinality explosion.

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
   - **Layer 2: User-specific premium rewritten XML cache (`user_feed_cache`)** - Caches the final, premium-rewritten RSS XML per user-show slug for 60 seconds.
   - **Layer 3: Permanent in-memory file size cache (`file_size_cache`)** - Caches resolved premium MP3 file sizes in-memory for ultra-fast, 0ms CPU-level lookup latency.
   - **Layer 4: Permanent database file size cache (`file_sizes` table)** - Caches resolved premium MP3 file sizes permanently in SQLite under the parameter-stripped clean `.mp3` path key, guaranteeing 100% cache hits across server restarts.

3. **Database Mapping Store (SQLite/JSON):**
   - Keeps a secure mapping of `user_id` (a cryptographic UUID) to the user's `__tac` cookie in table `users`.
   - Caches premium resolved file sizes in table `file_sizes` mapped by `public_url`.
   - **Lifespan Startup Sync:** Automatically creates and registers the `DEFAULT_USER_ID` with the active environment's `PIANO_TAC_COOKIE` on startup, and automatically synchronizes them if changed.

4. **Web Update Portal (`/`):**
   - A simple, clean, mobile-friendly HTML webpage served by FastAPI.
   - **Custom UUID Restore:** Allows users to type or paste their own custom/existing UUIDv4 during registration. This offers a seamless way to restore their identical accounts in case of database flushes, avoiding the need to change feed URLs inside their podcast player apps.
   - Displays copyable Option A RSS links for popular shows and provides a user-friendly setup guide.

5. **Observability Endpoint (`/metrics`):**
   - Serves Prometheus-compatible plain-text metrics.
   - **`omatasku_sessions_total`** (Gauge): Number of registered user sessions.
   - **`omatasku_last_registration_timestamp`** (Gauge): Epoch timestamp of the last session registration.
   - **`omatasku_http_requests_total`** (Counter): Access counts grouped by `method`, normalized `path` (replacing variables with route templates natively), and HTTP response `status`.
   - **`omatasku_outbound_head_requests_total`** (Counter): Outbound premium MP3 size HEAD request counts grouped by `show_slug` and returned HTTP status codes (or `status="error"` on exceptions).

6. **Distributed Tracing (OpenTelemetry):**
   - Fully instrumented with OpenTelemetry OTLP exporters.
   - Provides clean tracing spans for inbound HTTP requests, original RSS fetches, and parallel file size HEAD calls.
   - **Secure Cookie Masking:** Automatically censors the sensitive `__tac` cookie value from trace attributes (`omatasku.tac_cookie_preview`).
   - **Database Query Tracing:** Includes a custom `TracedConnection` proxy class inside `database.py` that intercepts all SQLite `execute()` transactions, automatically emitting `db.execute [OPERATION]` traces populated with standard `db.system`, `db.statement`, and `db.operation` attributes.

7. **Global Session Registration Rate Limiter:**
   - Incorporates a constant-memory, O(1) global rolling-window rate-limiting counter.
   - Restricts new session registrations (`POST /api/users`) to exactly 2 per minute, protecting the SQLite database from bulk-registration DoS attacks.
   - Does **not** apply any rate limits to cookie updates (`PUT /api/users/{user_id}`) or RSS streaming queries.

---

## 3. Building and Running

Refer to the main [README.md](./README.md) for full developer compilation instructions, docker execution environments, and native Uvicorn commands.

---

## 4. Development Conventions

- **Asynchronous I/O:** Leverage Python's `asyncio` and `httpx.AsyncClient` for outbound HTTP. Execute concurrent sub-tasks using `asyncio.gather` (e.g. parallel HEAD requests) to maximize throughput.
- **Robust XML Parsing:** Use standard libraries (`xml.etree.ElementTree`) for clean, safe, and portable parsing of RSS feeds. Always register namespaces to preserve prefix labels.
- **Graceful Error Handling & Defensive Programming:**
  - Skip empty, blank, or invalid URLs in resolution and size mapping immediately.
  - If a user's `__tac` cookie is expired or invalid, log a warning and fall back to returning the public preview feed instead of crashing.
- **Pylint Compliance:**
  - Ensure all python modules (`main.py`, `database.py`, `tracing.py`, `test_main.py`, `conftest.py`) achieve a perfect **10.00/10** compliance score under Pylint checks.
  - Strictly adhere to PEP 8 alphabetical import ordering, local state encapsulation, and native exception chaining (`raise ... from exc`).
- **Testing:** Include testing scripts using `pytest` and `httpx.AsyncClient`'s transport mocks to verify parsing, proxy rewriting, and database mapping state.
