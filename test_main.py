"""Unit and Integration Tests Module for OmaTasku.

Verifies user registrations, database updates, metrics rendering, proxy RSS XML parsing,
cookie validations, and security input-sanitizations under local-only mock environments.
"""

# Approved Pylint Disables
# pylint: disable=missing-function-docstring,missing-class-docstring
# pylint: disable=too-many-statements,line-too-long,trailing-whitespace

import os
from unittest.mock import patch, AsyncMock
import uuid

import pytest
from fastapi.testclient import TestClient

import database
import main
from main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_test_db():
    # Remove existing test DB if any
    if os.path.exists("test_omatasku.db"):
        os.remove("test_omatasku.db")
    database.init_db()
    yield
    # Clean up test DB after test run
    if os.path.exists("test_omatasku.db"):
        os.remove("test_omatasku.db")

def test_generate_uuid():
    response = client.get("/api/uuid")
    assert response.status_code == 200
    data = response.json()
    assert "uuid" in data
    # Verify it is a valid UUID
    val = uuid.UUID(data["uuid"])
    assert str(val) == data["uuid"]

def test_user_lifecycle():
    user_id = str(uuid.uuid4())
    tac_value = "initial.tac.cookie"
    comment = "My Test Phone"

    # 1. Register User
    reg_response = client.post("/api/users", json={
        "uuid": user_id,
        "tac_cookie": tac_value,
        "comment": comment
    })
    assert reg_response.status_code == 201
    reg_data = reg_response.json()
    assert reg_data["uuid"] == user_id
    assert reg_data["tac_cookie"] == tac_value
    assert reg_data["comment"] == comment
    assert "created_at" in reg_data
    assert "updated_at" in reg_data

    # 2. Get User Details
    get_response = client.get(f"/api/users/{user_id}")
    assert get_response.status_code == 200
    get_data = get_response.json()
    assert get_data["uuid"] == user_id
    assert get_data["tac_cookie"] == tac_value
    assert get_data["comment"] == comment

    # 3. Update User Cookie
    new_tac_value = "updated.tac.cookie"
    new_comment = "Updated Comment"
    put_response = client.put(f"/api/users/{user_id}", json={
        "tac_cookie": new_tac_value,
        "comment": new_comment
    })
    assert put_response.status_code == 200
    put_data = put_response.json()
    assert put_data["uuid"] == user_id
    assert put_data["tac_cookie"] == new_tac_value
    assert put_data["comment"] == new_comment
    assert put_data["updated_at"] != reg_data["updated_at"]

    # 4. Attempt to create duplicate user (should fail with 400)
    dup_response = client.post("/api/users", json={
        "uuid": user_id,
        "tac_cookie": "another.tac.cookie",
        "comment": "Dup"
    })
    assert dup_response.status_code == 400

    # 5. Retrieve non-existing user (should fail with 404)
    fake_id = str(uuid.uuid4())
    fake_response = client.get(f"/api/users/{fake_id}")
    assert fake_response.status_code == 404


@patch("main.httpx.AsyncClient")
def test_rss_proxy_cached(mock_client_class):
    # Setup mock HTTP client response
    mock_client = mock_client_class.return_value
    mock_client.__aenter__.return_value = mock_client
    
    # Store call counts to assert caching layer
    get_calls = []
    head_calls = []
    
    async def mock_get(url, *_args, **kwargs):
        get_calls.append(url)
        response = AsyncMock()
        response.status_code = 200
        response.raise_for_status = lambda: None
        
        if "ams.postimees.ee" in url:
            response.text = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Digitund</title><item><title>Digitund Episode</title><enclosure url="https://router.euddn.net/abc/preview/full/show-episodes/309848.mp3?c=8000&amp;ddnt=preview_sig" length="2970732" type="audio/mpeg"></enclosure></item></channel></rss>'
        elif "kuula.postimees.ee" in url:
            # Verify the correct tac cookie is forwarded in headers!
            assert "Cookie" in kwargs.get("headers", {})
            assert "__tac=test.tac.token" in kwargs["headers"]["Cookie"]
            response.json = lambda: {"309848": "https://router.euddn.net/abc/full/full/show-episodes/309848.mp3?c=8000&ddnt=premium_sig"}
            
        return response
        
    async def mock_head(url, *_args, **_kwargs):
        head_calls.append(url)
        response = AsyncMock()
        response.status_code = 200
        response.headers = {"Content-Length": "104857600"}  # Mock 100 MB file size
        return response
        
    mock_client.get = mock_get
    mock_client.head = mock_head
    
    # Reset global rate limiter before testing to prevent test-state interference
    main.registration_limiter["registration_count"] = 0
    main.registration_limiter["window_start"] = main.time.time()
    
    # Register a user ID first so the endpoint passes authentication
    user_id = str(uuid.uuid4())
    client.post("/api/users", json={
        "uuid": user_id,
        "tac_cookie": "test.tac.token",
        "comment": "RSS Tester"
    })
    
    # Clear any old global caches
    main.rss_cache.clear()
    main.user_feed_cache.clear()
    main.file_size_cache.clear()
    
    # 1. Fetch RSS first time
    # This must fetch the original RSS feed (1 call), resolve premium URLs via the Kuula API (1 call), 
    # and perform 1 HEAD request for file size = total 2 GET + 1 HEAD HTTP calls
    response1 = client.get(f"/{user_id}/postimees/rss/shows/digitund")
    assert response1.status_code == 200
    assert response1.headers["content-type"].startswith("application/rss+xml")
    
    # Verify the enclosure URL was successfully swapped in the returned XML!
    assert "https://router.euddn.net/abc/full/full/show-episodes/309848.mp3?c=8000&amp;ddnt=premium_sig" in response1.text
    # Verify enclosure length size is updated to the mocked 100 MB Content-Length!
    assert 'length="104857600"' in response1.text
    
    assert len(get_calls) == 2
    assert len(head_calls) == 1
    assert any("ams.postimees.ee" in u for u in get_calls)
    assert any("kuula.postimees.ee" in u for u in get_calls)
    assert any("abc/full/full" in u for u in head_calls)
    
    # Verify that the resolved size is successfully cached in the SQLite database permanently using the clean .mp3 path as the key!
    db_cached_size = database.get_file_size("https://router.euddn.net/abc/preview/full/show-episodes/309848.mp3")
    assert db_cached_size == "104857600"
    
    # 2. Fetch RSS second time (should hit the User Feed Cache directly, making 0 extra HTTP calls)
    response2 = client.get(f"/{user_id}/postimees/rss/shows/digitund")
    assert response2.status_code == 200
    assert "https://router.euddn.net/abc/full/full/show-episodes/309848.mp3?c=8000&amp;ddnt=premium_sig" in response2.text
    assert 'length="104857600"' in response2.text
    assert len(get_calls) == 2  # Still 2 calls, indicating no new network requests were executed!
    assert len(head_calls) == 1  # Still 1 call, showing file size cache is permanent!
    
    # 2b. Clear in-memory and user-feed caches, but NOT the SQLite database cache
    # The subsequent request must fetch the original RSS, resolve premium URLs, 
    # but resolve the file size natively from the SQLite database cache (without making a new HEAD request!).
    main.user_feed_cache.clear()
    main.file_size_cache.clear()
    
    response2b = client.get(f"/{user_id}/postimees/rss/shows/digitund")
    assert response2b.status_code == 200
    assert 'length="104857600"' in response2b.text
    assert len(head_calls) == 1  # Still 1 call, proving it was successfully loaded from the database cache, avoiding redundant HEAD requests!
    
    # 3. Retrieve with fake User ID (should fail with 404)
    fake_id = str(uuid.uuid4())
    fake_response = client.get(f"/{fake_id}/postimees/rss/shows/digitund")
    assert fake_response.status_code == 404

def test_prometheus_metrics():
    # Clear caches and tracking state before running
    main.http_metrics.clear()
    main.outbound_head_metrics.clear()
    main.rss_cache.clear()
    main.user_feed_cache.clear()
    main.file_size_cache.clear()
    
    # Reset global rate limiter before testing
    main.registration_limiter["registration_count"] = 0
    main.registration_limiter["window_start"] = main.time.time()
    
    # Register a user and trigger an RSS request to generate outbound HEAD metrics!
    user_id = str(uuid.uuid4())
    client.post("/api/users", json={
        "uuid": user_id,
        "tac_cookie": "test.tac.token",
        "comment": "Metrics Tester"
    })
    
    # Under mock, this triggers 1 HEAD request for digitund
    with patch("main.httpx.AsyncClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_client.__aenter__.return_value = mock_client
        
        async def mock_get(url, *_args, **_kwargs):
            response = AsyncMock()
            response.status_code = 200
            response.raise_for_status = lambda: None
            if "ams.postimees.ee" in url:
                response.text = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Digitund</title><item><title>Digitund Episode</title><enclosure url="https://router.euddn.net/abc/preview/full/show-episodes/400100.mp3?c=8000&amp;ddnt=preview_sig" length="2970732" type="audio/mpeg"></enclosure></item></channel></rss>'
            elif "kuula.postimees.ee" in url:
                response.json = lambda: {"400100": "https://router.euddn.net/abc/full/full/show-episodes/400100.mp3?c=8000&ddnt=premium_sig"}
            return response
            
        async def mock_head(_url, *_args, **_kwargs):
            response = AsyncMock()
            response.status_code = 200
            response.headers = {"Content-Length": "104857600"}
            return response
            
        mock_client.get = mock_get
        mock_client.head = mock_head
        
        # Fire standard requests
        client.get("/api/config")
        client.get("/api/uuid")
        client.get(f"/{user_id}/postimees/rss/shows/digitund")
        
        # Fire scanner requests
        client.get("/wp-login.php")
        client.get("/wp-admin/index.php")
    
    # 2. Fetch the /metrics endpoint
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    
    metrics_text = response.text
    
    # 3. Verify Database Metrics are reported correctly
    assert "# HELP omatasku_sessions_total" in metrics_text
    assert "# TYPE omatasku_sessions_total gauge" in metrics_text
    assert "omatasku_sessions_total " in metrics_text
    
    assert "# HELP omatasku_last_registration_timestamp" in metrics_text
    assert "omatasku_last_registration_timestamp " in metrics_text
    
    # 4. Verify HTTP Metrics are tracked with normalized paths (hiding sensitive UUIDs)
    assert "# HELP omatasku_http_requests_total" in metrics_text
    assert "# TYPE omatasku_http_requests_total counter" in metrics_text
    
    # Verify exact label counts and values are exported correctly
    assert 'omatasku_http_requests_total{method="GET",path="/api/config",status="200"}' in metrics_text
    assert 'omatasku_http_requests_total{method="GET",path="/api/uuid",status="200"}' in metrics_text
    
    # Verify the UUID and show slugs were successfully replaced with route parameter templates natively!
    assert 'omatasku_http_requests_total{method="GET",path="/{user_id}/postimees/rss/shows/{show_slug}",status="200"}' in metrics_text
    
    # Verify malicious scanner attempts are grouped and counted together under the static "*" path label!
    assert 'omatasku_http_requests_total{method="GET",path="*",status="404"}' in metrics_text
    
    # 5. Verify Outbound HEAD Metrics are tracked correctly
    assert "# HELP omatasku_outbound_head_requests_total" in metrics_text
    assert "# TYPE omatasku_outbound_head_requests_total counter" in metrics_text
    assert 'omatasku_outbound_head_requests_total{show_slug="digitund",status="200"}' in metrics_text
    
    # Ensure no raw UUID exists in the reported metrics
    assert user_id not in metrics_text

def test_userscript_mimetype():
    response = client.get("/static/omatasku.user.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    
    # Assert dynamic templating replaces the default URL and connect hostname correctly
    text = response.text
    assert main.BASE_URL in text
    assert "// @connect      localhost" in text

def test_favicon_ico():
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")

def test_security_sanitization():
    # Reset global rate limiter before testing to prevent test-state interference
    main.registration_limiter["registration_count"] = 0
    main.registration_limiter["window_start"] = main.time.time()

    # 1. Attempt registration with non-UUIDv4 characters (Pydantic validation error -> 422)
    bad_uuid_res = client.post("/api/users", json={
        "uuid": "malicious-user-id-with-special-characters-<script>",
        "tac_cookie": "header.payload.signature",
        "comment": "Safe Comment"
    })
    assert bad_uuid_res.status_code == 422

    # 2. Attempt registration with malicious __tac cookie (XSS / SQLi payload -> 422)
    bad_tac_res = client.post("/api/users", json={
        "uuid": str(uuid.uuid4()),
        "tac_cookie": "header.payload.signature-with-evil-<script>-tag",
        "comment": "Safe Comment"
    })
    assert bad_tac_res.status_code == 422

    # 3. Attempt registration with malicious comment (HTML / Script injection -> 422)
    bad_comment_res = client.post("/api/users", json={
        "uuid": str(uuid.uuid4()),
        "tac_cookie": "header.payload.signature",
        "comment": "My Phone <script>alert(1)</script>"
    })
    assert bad_comment_res.status_code == 422

    # 4. Attempt registration with valid unicode and punctuation comment (Should succeed -> 201)
    good_user_id = str(uuid.uuid4())
    good_res = client.post("/api/users", json={
        "uuid": good_user_id,
        "tac_cookie": "header.payload.signature",
        "comment": "Minu Telefon - ÕÄÖÜ, Кириллица."
    })
    assert good_res.status_code == 201

    # Register a second valid user (succeeds -> 201)
    good_user_id2 = str(uuid.uuid4())
    good_res2 = client.post("/api/users", json={
        "uuid": good_user_id2,
        "tac_cookie": "header.payload.signature",
        "comment": "Teine Kasutaja"
    })
    assert good_res2.status_code == 201

    # 5. Attempt a third registration immediately (should trigger global rate limit -> 429!)
    rate_limited_res = client.post("/api/users", json={
        "uuid": str(uuid.uuid4()),
        "tac_cookie": "header.payload.signature",
        "comment": "Kolmas Kasutaja"
    })
    assert rate_limited_res.status_code == 429
    assert "ülemaailmne limiit on ületatud" in rate_limited_res.json()["detail"]

    # 6. Verify that cookie updates (PUT) are NOT rate-limited (should succeed -> 200!)
    update_res = client.put(f"/api/users/{good_user_id}", json={
        "tac_cookie": "newheader.newpayload.newsignature",
        "comment": "Still Allowed"
    })
    assert update_res.status_code == 200

    # 7. Verify invalid UUID path parameters are rejected with 400 Bad Request
    invalid_path_res = client.get("/api/users/non-uuid-path-param")
    assert invalid_path_res.status_code == 400
    assert "UUIDv4 on kohustuslik" in invalid_path_res.json()["detail"]

    # 8. Verify invalid show slugs (e.g. script injection payloads) are rejected with 400 Bad Request
    invalid_slug_res = client.get(f"/{good_user_id}/postimees/rss/shows/digitund<script>")
    assert invalid_slug_res.status_code == 400
    assert "Lubatud on ainult tähed, numbrid, kriipsud ja alakriipsud" in invalid_slug_res.json()["detail"]
