# GEMINI.md - OmaTasku System Instructions (For AI Agents)

> **IMPORTANT NOTE:** This file (`GEMINI.md`) is strictly reserved as system instructions and context guidelines for **AI Agents** (such as Gemini CLI). For normal human user instructions, deployment guides, and developer setups, please refer to the main [README.md](./README.md) instead.

---

This file serves as the core system instruction, architectural specification, and context guidelines for the **OmaTasku** project. It details the system's design, protocols, data contracts, and workflows in a completely **programming-language-agnostic** way, enabling clean and identical reimplementation in **Go, Rust, Node.js, C++**, or any other technology stack.

---

## 1. Project Overview & Domain Research

**OmaTasku** (meaning "Own Podcast" in Estonian) is a lightweight premium podcast RSS feed mirroring proxy. It intercepts public podcast RSS feeds that contain truncated preview (teaser) episodes and dynamically swaps their preview enclosure URLs and file sizes with fully-playable, premium-length CDNs and true file lengths. 

Authorization is completed using a valid subscriber session token cookie. To prevent podcast player URL breakage when session cookies expire, OmaTasku maps a permanent, custom cryptographic User ID (UUID) in a local database to the active subscriber cookie, allowing users to update their cookies seamlessly behind the scenes without changing their feed links.

### Target Platform API Mechanics

#### A. Authentication & Session Cookie
- The platform identity provider sets a long-lived subscriber cookie named **`__tac`** containing a Base64URL-encoded JWT (JSON Web Token) of exactly three dot-separated segments.
- Passing this `__tac` cookie in the `Cookie` header of audio resolution API requests grants authorized, full-length audio tracks.

#### B. Public Show RSS Feeds
- Original RSS feeds are hosted at: `https://ams.postimees.ee/rss/shows/{show_slug}`.
- Enclosure elements point to teaser files on the CDN (`router.example.net`):
  `https://router.example.net/[hash]/preview/full/show-episodes/[id].mp3?c=8000&ddnt=[preview_signature]`

#### C. Audio URL Resolution API
- To play/download an episode, OmaTasku queries the platform backend resolver:
  `GET https://kuku.postimees.ee/api/proxy/ams/kuula/episodes/urls?ids={episode_id}`
- **Response if Unauthenticated / Public:** Returns a CDN URL containing the `/preview/` path segment and a signature valid only for the ~74s snippet.
- **Response if Subscribed:** (Forwarding the active `__tac` cookie) Returns a CDN URL containing the `/full/` segment and a premium cryptographic signature valid for the entire episode:
  `https://router.example.net/[hash]/full/full/show-episodes/[id].mp3?c=8000&ddnt=[premium_signature]`
- The CDN verifies path signatures; modifying `/preview/` to `/full/` manually is rejected with `403 Forbidden`.

#### D. Enclosure File Size Correction
- original feeds contain teaser file lengths (~3MB). Since premium files are significantly larger (~100MB+), podcast players get confused during download and streaming.
- OmaTasku performs fast concurrent outbound HTTP **`HEAD`** requests to the resolved premium CDN links and extracts the **`Content-Length`** header, caching them permanently inside a database.

---

## 2. Language-Agnostic System Architecture & Data Contracts

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

An identical service must implement the following **six data contracts and algorithmic loops**:

### A. Database Schema Specification
The local storage must maintain two tables with strict format validations:

1. **`users` Table:**
   - `uuid` (String, Primary Key): A valid UUIDv4 string.
   - `tac_cookie` (String, Not Null): Cleaned, valid `__tac` JWT token. Must match: `^[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+$`.
   - `comment` (String, Nullable, max 100 chars): Optional user identifier, restricted to unicode alphanumeric letters and safe punctuation (` `, `.`, `,`, `-`, `_`).
   - `created_at` (String, ISO-8601).
   - `updated_at` (String, ISO-8601).

2. **`file_sizes` Table:**
   - `public_url` (String, Primary Key): The **clean, query-stripped** public enclosure `.mp3` path key (stripped of any dynamic `?c=8000&ddnt=...` parameters) to ensure cache key stability.
   - `content_length` (String, Not Null): The resolved full premium MP3 size in bytes.
   - `created_at` (String, ISO-8601).

### B. Core Mirroring Execution Loop (GET `/{user_id}/postimees/rss/shows/{show_slug}`)
When a podcast player hits this route:
1. **Validation:**
   - Verify `user_id` matches UUIDv4 format, returning `400` if invalid.
   - Verify `show_slug` matches `^[a-zA-Z0-9_\-]+$`, returning `400` if invalid (prevents path traversal/SSRF).
2. **User Lookup & Active Session Validation (Offline):**
   - Query `users` by `user_id`. If not found, fall back gracefully to returning the public original RSS XML.
   - **Offline JWT Expiry Check:** Decode the `__tac` JWT token natively to extract its `exp` (expiration) epoch claim. If the `exp` claim is in the past, immediately raise a `403 Forbidden` exception so that podcast players (like AntennaPod) can report the authentication error.
3. **Feed Caching (Layer 1 & 2):**
   - Check per-user feed XML cache for key `(user_id, show_slug)`. If hit and unexpired, return XML instantly with its cached Content-Type.
   - Check global original RSS feed XML cache for key `show_slug`. If missed, fetch from `https://ams.postimees.ee/rss/shows/{show_slug}` and write to original XML cache (TTL: 60s).
4. **Episode Extraction:**
   - Parse XML and extract `<enclosure>` tags.
   - Gather all unique numerical episode IDs using regex `(\d+)\.mp3` on the enclosure URL.
   - Keep a map of `{episode_id: original_url}`.
5. **Batch Premium URL Resolution & Active Session Validation (Online):**
   - Send `GET https://kuku.postimees.ee/api/proxy/ams/kuula/episodes/urls?ids={comma_separated_ids}` with header `Cookie: __tac={user.tac_cookie}` (explicitly following HTTP redirects).
   - Returns a JSON dictionary mapping `{episode_id: premium_url}`.
   - **Online Revocation Check:** If the resolver API returned successful responses, but **ALL** returned URLs contain the `/preview/` segment instead of `/full/`, it means the session has been revoked, is invalid, or has no active premium subscription. In this case, raise a `403 Forbidden` exception directly.
6. **Concurrent File Size Resolution (Layer 3 & 4):**
   - For each valid `premium_url`:
     - Strip query parameters from `original_url` to get a clean key: `clean_public_url = original_url.split("?")[0]`.
     - Check memory cache. If hit, return size.
     - Check DB table `file_sizes` using `clean_public_url`. If hit, write to memory cache and return.
     - On cache miss, execute an asynchronous concurrent HTTP `HEAD` request to `premium_url` (following redirects). Extract the `Content-Length` header, populate the memory cache, and insert/replace permanently into the `file_sizes` table using `clean_public_url`.
7. **XML Rewriting & Response:**
   - Re-serialize the XML, swapping the preview `<enclosure url="...">` and `<enclosure length="...">` attributes with their premium equivalents.
   - Cache the final premium XML globally for 60 seconds and return with the **exact, preserved Content-Type and Charset encoding of the original feed** (such as `application/rss+xml; charset=UTF-8`), preserving character rendering integrity.

### C. Web Dashboard UI & Cookie Synchronization APIs
- **`GET /`**: Serves a static HTML client (must accept both `GET` and `HEAD` requests for uptime monitoring).
- **`GET /api/uuid`**: Generates and returns a fresh, cryptographically secure random UUIDv4.
- **`POST /api/users`**: Registers a new user. Expects `UserCreate` JSON, runs rate limiting, schema validations, and inserts into `users`.
- **`GET /api/users/{user_id}`**: Fetches registered user meta.
- **`PUT /api/users/{user_id}`**: Updates existing user session cookie. Exempt from rate limiting.
- **`GET /static/omatasku.user.js`**: Dynamically serves the Violentmonkey userscript, replacing `"http://localhost:8080"` with the active host's `BASE_URL` and setting browser-safe JavaScript content-types.

### D. Global Rate Limiter Specification (Constant-Memory O(1) State)
- Implement a global rolling-window session creation rate-limiting counter:
  - State: `registration_count` (integer), `window_start` (float timestamp).
  - Algorithm: On `POST /api/users`, if `now() - window_start >= 60 seconds`, reset `window_start = now()` and `registration_count = 0`.
  - If `registration_count >= 2`, reject with `429 Too Many Requests`. Otherwise, increment `registration_count` and allow.
  - No database list, memory expansion, or IP maps are stored, keeping memory overhead completely constant (O(1)).

### E. Observability & Telemetry Data Contracts
Expose a public plain-text endpoint `/metrics` for Prometheus scraping, reporting:
- **`omatasku_sessions_total`** (Gauge): Number of active user records in DB.
- **`omatasku_last_registration_timestamp`** (Gauge): ISO-8601 float epoch.
- **`omatasku_http_requests_total`** (Counter): HTTP queries processed, with label keys `method`, `path`, and `status`. **Constraint:** All incoming requests MUST be matched against registered route templates to prevent label cardinality explosion. Unmatched scanner requests (like WP exploits) must be grouped under a single static fallback label `path="*"`.
- **`omatasku_outbound_head_requests_total`** (Counter): Outbound MP3 size HEAD request counts with labels `show_slug` and `status` (or `status="error"` on exceptions).

---

## 3. Building and Running

Refer to the main [README.md](./README.md) for full developer compilation instructions, docker execution environments, and native Uvicorn commands.

---

## 4. Development Conventions

- **Asynchronous I/O:** Leverage standard asynchronous runtimes (like `asyncio` in Python, Goroutines in Go, or Tokio in Rust) to fire concurrent outbound HEAD/resolution calls, optimizing throughput.
- **Pylint / Lint Compliance:** Maintain perfect compliance under standard linter suites (strict import order, complete encapsulation, zero inline disables without explicit approvals, and standard exception chaining `raise ... from exc`).
- **Testing:** Include 100% test coverage using standard test suites (mocks, local database sandboxing, and mime-type assertions).
