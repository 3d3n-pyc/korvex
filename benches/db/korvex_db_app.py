"""The database benchmark's app, in Korvex: GET /users/{id:int} reads one
row from PostgreSQL (asyncpg) and returns it as JSON. Each event loop
(each worker, and each thread with threads=) opens its own pool when it
starts, kept in app.local."""

import os

import asyncpg

from korvex import App, HTTPException, Response

app = App()


@app.on_startup
async def connect():
    app.local.pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=4, max_size=10)


@app.on_shutdown
async def disconnect():
    await app.local.pool.close()


@app.get("/users/{id:int}")
async def get_user(request, id):
    row = await app.local.pool.fetchrow("SELECT id, name FROM users WHERE id = $1", id)
    if row is None:
        raise HTTPException(404)
    return Response.json({"id": row["id"], "name": row["name"]})
