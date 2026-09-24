import pytest
from fastapi.testclient import TestClient

from app.main import app, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_store():
    # 테스트끼리 저장소를 공유하지 않게 매번 비운다
    store.reset()


def test_healthz():
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.text == "ok"


def test_info():
    body = client.get("/").json()
    assert body["service"] == "python-api"
    assert {"environment", "version", "hostname"} <= body.keys()


def test_crud_flow():
    res = client.post("/items", json={"name": "pen", "price": 1.5})
    assert res.status_code == 201
    item = res.json()
    assert item == {"id": 1, "name": "pen", "description": None, "price": 1.5}

    assert client.get("/items").json() == [item]
    assert client.get("/items/1").json() == item

    res = client.put("/items/1", json={"name": "pencil", "description": "HB", "price": 2})
    assert res.status_code == 200
    assert res.json()["name"] == "pencil"

    assert client.delete("/items/1").status_code == 204
    assert client.get("/items").json() == []


def test_ids_increase():
    ids = [client.post("/items", json={"name": f"n{i}", "price": 0}).json()["id"] for i in range(3)]
    assert ids == [1, 2, 3]


@pytest.mark.parametrize(
    "method,path",
    [("get", "/items/99"), ("delete", "/items/99")],
)
def test_not_found(method, path):
    assert getattr(client, method)(path).status_code == 404


def test_update_not_found():
    assert client.put("/items/99", json={"name": "x", "price": 0}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [{"price": 1}, {"name": "", "price": 1}, {"name": "x", "price": -1}],
)
def test_validation(payload):
    assert client.post("/items", json=payload).status_code == 422
