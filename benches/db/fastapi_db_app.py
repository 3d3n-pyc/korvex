"""The database benchmark's app, in FastAPI (see korvex_db_app.py)."""

import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException

pool = None


@asynccontextmanager
async def lifespan(app):
    global pool
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=4, max_size=10)
    yield
    await pool.close()


app = FastAPI(lifespan=lifespan)


@app.get("/users/{id}")
async def get_user(id: int):
    row = await pool.fetchrow("SELECT id, name FROM users WHERE id = $1", id)
    if row is None:
        raise HTTPException(404)
    return {"id": row["id"], "name": row["name"]}
