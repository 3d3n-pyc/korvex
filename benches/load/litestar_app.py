"""The load-test app, in Litestar (see korvex_app.py)."""

from litestar import Litestar, get


async def filler(id: int) -> str:
    return "filler"


@get("/users/{id:int}")
async def get_user(id: int) -> dict:
    return {"id": id, "name": f"user {id}"}


fillers = [get(f"/r{i % 10}/{{id:int}}/detail_{i}", name=f"filler_{i}")(filler) for i in range(1_000)]
app = Litestar([*fillers, get_user])
