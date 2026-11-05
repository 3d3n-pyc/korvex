"""A small app used by every deployment in this folder."""

from korvex import App, Response

app = App()


@app.on_startup
def startup():
    print("korvex app started", flush=True)


@app.get("/")
def home(request):
    return Response.text("Hello from Korvex")


@app.get("/users/{id:int}")
def get_user(request, id):
    # Behind a trusted proxy, client and scheme are the real ones, not the proxy's.
    return Response.json({"id": id, "client": request.client, "scheme": request.scheme})
