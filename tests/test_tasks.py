import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKBOARD_DB", str(tmp_path / "test.db"))
    from app.main import app
    return TestClient(app)


def test_create_and_list(client):
    r = client.post("/api/tasks", json={"title": "Comprar leche"})
    assert r.status_code == 201
    data = client.get("/api/tasks?page=0").json()
    assert any(t["title"] == "Comprar leche" for t in data["items"])


def test_create_without_priority_defaults_to_medium(client):
    r = client.post("/api/tasks", json={"title": "X"})
    assert r.status_code == 201
    assert r.json()["priority"] == "medium"
    assert client.get("/api/tasks").json()["items"][0]["priority"] == "medium"


@pytest.mark.parametrize("priority", ["low", "medium", "high"])
def test_create_persists_chosen_priority(client, priority):
    r = client.post("/api/tasks", json={"title": "X", "priority": priority})
    assert r.status_code == 201
    assert r.json()["priority"] == priority
    assert client.get("/api/tasks").json()["items"][0]["priority"] == priority


def test_create_rejects_unknown_priority(client):
    r = client.post("/api/tasks", json={"title": "X", "priority": "urgent"})
    assert r.status_code == 422
    assert client.get("/api/tasks").json()["total"] == 0


def test_list_filters_by_priority(client):
    for priority in ["low", "high", "high"]:
        client.post("/api/tasks", json={"title": "X", "priority": priority})

    high = client.get("/api/tasks?priority=high").json()

    assert high["total"] == 2
    assert [t["priority"] for t in high["items"]] == ["high", "high"]
    assert client.get("/api/tasks?priority=low").json()["total"] == 1
    assert client.get("/api/tasks?priority=medium").json()["total"] == 0
    assert client.get("/api/tasks?priority=unknown").json()["total"] == 0
    assert client.get("/api/tasks").json()["total"] == 3


def test_list_combines_status_and_priority_filters(client):
    ids = [
        client.post("/api/tasks", json={"title": "X", "priority": p}).json()["id"]
        for p in ["high", "high", "low"]
    ]
    client.patch(f"/api/tasks/{ids[0]}/toggle")
    client.patch(f"/api/tasks/{ids[2]}/toggle")

    data = client.get("/api/tasks?status=done&priority=high").json()

    assert data["total"] == 1
    assert [t["id"] for t in data["items"]] == [ids[0]]


def test_existing_database_gains_priority_and_keeps_rows(client, tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    conn.execute(
        """CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute("INSERT INTO tasks (title, status) VALUES ('Old', 'done')")
    conn.commit()
    conn.close()

    data = client.get("/api/tasks").json()

    assert data["total"] == 1
    assert data["items"][0]["title"] == "Old"
    assert data["items"][0]["status"] == "done"
    assert data["items"][0]["priority"] == "medium"
    assert client.get("/api/tasks").json()["total"] == 1


def test_pagination_starts_at_first_record(client):
    created = [
        client.post("/api/tasks", json={"title": f"Task {i}"}).json()["id"]
        for i in range(12)
    ]

    first = client.get("/api/tasks?page=1&page_size=10").json()
    second = client.get("/api/tasks?page=2&page_size=10").json()

    assert [t["id"] for t in first["items"]] == created[:10]
    assert [t["id"] for t in second["items"]] == created[10:]


def test_total_respects_status_filter(client):
    ids = [
        client.post("/api/tasks", json={"title": f"Task {i}"}).json()["id"]
        for i in range(12)
    ]
    for task_id in ids[:3]:
        client.patch(f"/api/tasks/{task_id}/toggle")

    assert client.get("/api/tasks?status=done").json()["total"] == 3
    assert client.get("/api/tasks?status=todo").json()["total"] == 9
    assert client.get("/api/tasks?status=unknown").json()["total"] == 0
    assert client.get("/api/tasks").json()["total"] == 12


def test_toggle(client):
    task = client.post("/api/tasks", json={"title": "X"}).json()
    r = client.patch(f"/api/tasks/{task['id']}/toggle")
    assert r.json()["status"] == "done"


def test_delete(client):
    task = client.post("/api/tasks", json={"title": "X"}).json()
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 404
