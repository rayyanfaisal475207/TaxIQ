"""
HTTP layer — auth, ownership, downloads.

Guards:
  * every data route requires authentication
  * a user cannot read, rename, or delete another user's session
  * downloads carry a real filename + MIME type (they used to be extensionless
    application/octet-stream, which Windows can't open)
  * admin routes require the is_admin flag, not merely a valid login
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.auth.routes import get_current_user
from src.auth.jwt import require_admin
from src.data_gateway import get_gateway as real_get_gateway


class _User:
    def __init__(self, user_id, is_admin=False):
        self.id = uuid.UUID(user_id)
        self.email = "test@example.com"
        self.is_admin = is_admin
        self.company_name = "TestCo"
        self.plan = "free"


@pytest.fixture
def api(gateway, user_id, monkeypatch):
    """TestClient with auth and the data gateway faked out."""
    async def _get_gateway():
        return gateway

    # Patch the package attribute too: several endpoints import get_gateway
    # lazily inside the function body (`from src.data_gateway import get_gateway`),
    # which resolves against the package at call time.
    for module in ("src.data_gateway", "src.data_gateway.selector", "src.main",
                   "src.api.sessions", "src.api.projects", "src.api.profile",
                   "src.api.admin"):
        monkeypatch.setattr(f"{module}.get_gateway", _get_gateway, raising=False)

    app.dependency_overrides[get_current_user] = lambda: _User(user_id)
    app.dependency_overrides[real_get_gateway] = _get_gateway

    yield TestClient(app), gateway

    app.dependency_overrides.clear()


@pytest.fixture
def admin_api(api, user_id):
    client, gateway = api
    app.dependency_overrides[get_current_user] = lambda: _User(user_id, is_admin=True)
    app.dependency_overrides[require_admin] = lambda: _User(user_id, is_admin=True)
    return client, gateway


# ── Authentication ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ("get", "/api/sessions"),
    ("get", "/api/attachments"),
    ("post", "/api/chat"),
    ("get", "/api/admin/metrics"),
    ("get", "/api/admin/kb/stats"),
    ("get", "/api/admin/errors"),
])
def test_data_routes_require_authentication(method, path):
    """
    No dependency override installed → the real auth dependency must reject the
    request before the endpoint body (and therefore before any DB access) runs.
    TestClient is deliberately NOT used as a context manager: that would run the
    app lifespan, which tries to reach the database.
    """
    client = TestClient(app)

    request = getattr(client, method)
    response = request(path, json={}) if method in ("post", "patch", "put") else request(path)

    assert response.status_code in (401, 403), f"{path} is reachable unauthenticated"


def test_health_is_public(api):
    client, _ = api
    assert client.get("/health").status_code == 200


# ── Session ownership ─────────────────────────────────────────────────────────

def test_lists_only_the_callers_sessions(api, user_id, session_id):
    client, gateway = api
    gateway.sessions[session_id] = {
        "session_id": session_id, "user_id": user_id, "project_id": None, "title": "Mine",
    }
    other = str(uuid.uuid4())
    gateway.sessions[other] = {
        "session_id": other, "user_id": str(uuid.uuid4()), "project_id": None, "title": "Theirs",
    }

    response = client.get("/api/sessions")

    assert response.status_code == 200
    assert [s["title"] for s in response.json()] == ["Mine"]


def test_reads_own_session_history(api, user_id, session_id):
    client, gateway = api
    gateway.sessions[session_id] = {
        "session_id": session_id, "user_id": user_id, "project_id": None, "title": "T",
    }
    gateway.messages.append({"message_id": "m1", "session_id": session_id,
                             "role": "user", "content": "hello"})

    response = client.get(f"/api/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["history"][0]["content"] == "hello"


@pytest.mark.parametrize("method,payload", [
    ("get", None),
    ("delete", None),
    ("patch", {"title": "hijacked"}),
])
def test_cannot_touch_another_users_session(api, session_id, method, payload):
    client, gateway = api
    gateway.sessions[session_id] = {
        "session_id": session_id, "user_id": str(uuid.uuid4()),  # someone else
        "project_id": None, "title": "Theirs",
    }

    request = getattr(client, method)
    path = f"/api/sessions/{session_id}"
    response = request(path, json=payload) if payload is not None else request(path)

    assert response.status_code == 403


def test_missing_session_is_404(api):
    client, _ = api
    assert client.get(f"/api/sessions/{uuid.uuid4()}").status_code == 404


# ── Downloads ─────────────────────────────────────────────────────────────────

@pytest.fixture
def generated_file(gateway, user_id, tmp_path):
    path = tmp_path / "export.xlsx"
    path.write_bytes(b"PK\x03\x04fake-xlsx")
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": user_id,
        "file_type": "xlsx", "file_name": "WHT Rate Card.xlsx",
        "file_size_bytes": path.stat().st_size, "storage_path": str(path),
    }
    return file_id


def test_download_sends_the_correct_mime_and_filename(api, generated_file):
    """Regression: everything was served as extensionless octet-stream."""
    client, _ = api

    response = client.get(f"/api/files/{generated_file}/download")

    assert response.status_code == 200
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert ".xlsx" in response.headers["content-disposition"]


def test_download_repairs_a_legacy_extensionless_filename(api, gateway, user_id, tmp_path):
    """Old rows stored the bare title; the response must still name the file usably."""
    path = tmp_path / "old.pdf"
    path.write_bytes(b"%PDF-1.4 fake")
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": user_id,
        "file_type": "pdf", "file_name": "WHT Rate Card",  # no extension
        "file_size_bytes": 13, "storage_path": str(path),
    }
    client, _ = api

    response = client.get(f"/api/files/{file_id}/download")

    assert ".pdf" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "application/pdf"


def test_cannot_download_another_users_file(api, gateway, tmp_path):
    path = tmp_path / "theirs.pdf"
    path.write_bytes(b"%PDF-1.4")
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "file_type": "pdf", "file_name": "Theirs.pdf",
        "file_size_bytes": 8, "storage_path": str(path),
    }
    client, _ = api

    assert client.get(f"/api/files/{file_id}/download").status_code == 403


def test_admin_may_download_any_users_file(admin_api, gateway, tmp_path):
    """The admin panel lists every user's files — it must be able to fetch them."""
    path = tmp_path / "theirs.pdf"
    path.write_bytes(b"%PDF-1.4")
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),
        "file_type": "pdf", "file_name": "Theirs.pdf",
        "file_size_bytes": 8, "storage_path": str(path),
    }
    client, _ = admin_api

    assert client.get(f"/api/files/{file_id}/download").status_code == 200


def test_malformed_file_id_is_rejected(api):
    client, _ = api
    assert client.get("/api/files/not-a-uuid/download").status_code == 400


def test_download_of_a_vanished_file_is_404(api, gateway, user_id):
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": user_id,
        "file_type": "pdf", "file_name": "Gone.pdf", "file_size_bytes": 1,
        "storage_path": "/nonexistent/gone.pdf",
    }
    client, _ = api

    assert client.get(f"/api/files/{file_id}/download").status_code == 404


# ── Admin ─────────────────────────────────────────────────────────────────────

def test_admin_delete_removes_record_and_file_from_disk(admin_api, gateway, user_id, tmp_path):
    """Regression: this 500'd — the direct backend returned only {file_id}."""
    path = tmp_path / "doomed.pdf"
    path.write_bytes(b"%PDF-1.4")
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": user_id,
        "file_type": "pdf", "file_name": "Doomed.pdf", "file_size_bytes": 8,
        "storage_path": str(path),
    }
    client, _ = admin_api

    response = client.delete(f"/api/admin/files/{file_id}")

    assert response.status_code == 200
    assert file_id not in gateway.files
    assert not os.path.exists(path), "the file was left on disk"


def test_admin_delete_of_a_record_with_no_disk_file_still_succeeds(admin_api, gateway, user_id):
    file_id = str(uuid.uuid4())
    gateway.files[file_id] = {
        "file_id": file_id, "session_id": str(uuid.uuid4()), "user_id": user_id,
        "file_type": "pdf", "file_name": "X.pdf", "file_size_bytes": 1,
        "storage_path": "/nonexistent/x.pdf",
    }
    client, _ = admin_api

    assert client.delete(f"/api/admin/files/{file_id}").status_code == 200


def test_admin_metrics_include_the_fields_the_dashboard_renders(admin_api):
    """Regression: the direct backend omitted route_metrics/table_stats."""
    client, _ = admin_api

    body = client.get("/api/admin/metrics").json()

    assert "route_metrics" in body
    assert "table_stats" in body
