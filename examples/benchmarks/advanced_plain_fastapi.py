from __future__ import annotations

import asyncio

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class ItemPayload(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None
    tags: list[str] = []


@app.post("/items")
async def create_item(item: ItemPayload) -> dict[str, str]:
    # Simulate database / external service latency
    await asyncio.sleep(0.015)
    return {"status": "created", "name": item.name}
