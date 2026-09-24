"""python-api: 메모리 저장소를 쓰는 FastAPI CRUD 예제.

데이터는 프로세스 메모리에만 있다. 파드가 재시작되면 사라지고, 파드가 둘 이상이면
파드마다 목록이 다르다. 그래서 매니페스트의 replicas 는 1 이다(README 8장).
"""

import os
import socket
import threading

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

# APP_ENV 는 Deployment 의 env(overlay 가 dev/prod 로 바꾼다),
# APP_VERSION 은 빌드 때 Dockerfile 의 ARG 로 이미지에 들어간다(이미지 태그).
APP_ENV = os.getenv("APP_ENV", "local")
APP_VERSION = os.getenv("APP_VERSION", "local")


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    price: float = Field(ge=0)


class Item(ItemIn):
    id: int


class ItemStore:
    """스레드 안전한 메모리 저장소.

    FastAPI 는 async 가 아닌 엔드포인트를 스레드 풀에서 돌리므로 잠금이 필요하다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._items: dict[int, Item] = {}
            self._next_id = 1

    def list(self) -> list[Item]:
        with self._lock:
            return list(self._items.values())

    def get(self, item_id: int) -> Item | None:
        with self._lock:
            return self._items.get(item_id)

    def create(self, data: ItemIn) -> Item:
        with self._lock:
            item = Item(id=self._next_id, **data.model_dump())
            self._items[item.id] = item
            self._next_id += 1
            return item

    def update(self, item_id: int, data: ItemIn) -> Item | None:
        with self._lock:
            if item_id not in self._items:
                return None
            item = Item(id=item_id, **data.model_dump())
            self._items[item_id] = item
            return item

    def delete(self, item_id: int) -> bool:
        with self._lock:
            return self._items.pop(item_id, None) is not None


store = ItemStore()
app = FastAPI(title="python-api", version=APP_VERSION)


def _not_found(item_id: int) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"item {item_id} not found")


@app.get("/")
def info() -> dict:
    # hostname 은 응답한 파드 이름이다. 어느 파드가 받았는지 볼 때 쓴다
    return {
        "service": "python-api",
        "environment": APP_ENV,
        "version": APP_VERSION,
        "hostname": socket.gethostname(),
    }


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/items")
def list_items() -> list[Item]:
    return store.list()


@app.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(data: ItemIn) -> Item:
    return store.create(data)


@app.get("/items/{item_id}")
def get_item(item_id: int) -> Item:
    item = store.get(item_id)
    if item is None:
        raise _not_found(item_id)
    return item


@app.put("/items/{item_id}")
def update_item(item_id: int, data: ItemIn) -> Item:
    item = store.update(item_id, data)
    if item is None:
        raise _not_found(item_id)
    return item


@app.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int) -> Response:
    if not store.delete(item_id):
        raise _not_found(item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
