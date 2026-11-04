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
    return Response.json({"id": id, "forwarded_for": request.headers.get("x-forwarded-for")})
