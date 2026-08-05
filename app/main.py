"""FlareCredit  application entrypoint.

IF YOU SEE THE OLD BEHAVIOUR (/ shows the dashboard, /docs spins forever,
/support 404s) THEN THIS FILE IS NOT THE ONE RUNNING. Verify with:

    python -c "import app.main; print(app.main.FC_BUILD)"

It must print: fc-site-v2

Routes
------
    /            landing            static/fc-landing.html
    /docs        documentation      static/fc-docs.html
    /support     support            static/fc-support.html
    /app         dashboard          static/app/index.html
    /api/...     JSON API
    /api-docs    Swagger UI (moved so /docs can be the docs page)
    /__routes    lists every registered route (debug)
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router
from .fc_support import router as fc_support_router

FC_BUILD = "fc-site-v2"          # <-- sentinel used by the check above

app = FastAPI(
    title="FlareCredit",
    version="2.0.0",
    docs_url="/api-docs",        # Swagger moved: /docs is the documentation page
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

app.include_router(router)
app.include_router(fc_support_router)

STATIC = Path(__file__).resolve().parent.parent / "static"


def _page(name: str) -> FileResponse:
    f = STATIC / name
    if not f.exists():
        raise RuntimeError(
            f"{name} is missing from {STATIC}. Copy the fc-*.html files into static/."
        )
    return FileResponse(f)


# ------------------------------------------------------------------ pages
@app.get("/", include_in_schema=False)
def page_landing():
    return _page("fc-landing.html")


# Documentation is a multi-page site: /docs plus /docs/<slug>
DOC_SLUGS = [
    "concepts", "quickstart", "identity", "attesting", "scoring", "borrowing",
    "troubleshooting", "architecture", "score-model", "privacy", "security",
    "contracts", "api", "glossary", "faq",
]


@app.get("/docs", include_in_schema=False)
def page_docs():
    return _page("fc-docs.html")


@app.get("/docs/{slug}", include_in_schema=False)
def page_docs_section(slug: str):
    if slug not in DOC_SLUGS:
        return JSONResponse({"detail": "Doc page not found"}, status_code=404)
    return _page(f"fc-docs-{slug}.html")


@app.get("/support", include_in_schema=False)
def page_support():
    return _page("fc-support.html")


@app.get("/__routes", include_in_schema=False)
def list_routes():
    """Debug: confirm which build is live and what's registered."""
    return JSONResponse({
        "build": FC_BUILD,
        "static_dir": str(STATIC),
        "static_exists": STATIC.exists(),
        "has_landing": (STATIC / "fc-landing.html").exists(),
        "has_docs": (STATIC / "fc-docs.html").exists(),
        "has_support": (STATIC / "fc-support.html").exists(),
        "has_dashboard": (STATIC / "app" / "index.html").exists(),
        "routes": sorted(getattr(r, "path", str(r)) for r in app.routes),
    })


# ------------------------------------------------------- dashboard at /app
_APP_DIR = STATIC / "app"

# Explicit route so BOTH /app and /app/ work. A bare StaticFiles mount only
# answers "/app/"  every "Launch app" link points at "/app", which would 404.
@app.get("/app", include_in_schema=False)
def page_app():
    idx = _APP_DIR / "index.html"
    if not idx.exists():
        return JSONResponse(
            {"error": "Dashboard not found",
             "expected": str(idx),
             "fix": "Create static/app/ and move the dashboard index.html, app.js "
                    "and styles.css into it."},
            status_code=500,
        )
    return FileResponse(idx)


if _APP_DIR.exists():
    app.mount("/app", StaticFiles(directory=_APP_DIR, html=True), name="dashboard")

# ------------------------------------------------------------- static assets
# Mounted LAST and WITHOUT html=True, so it can never shadow the pages above
# or auto-serve an index.html at "/".
app.mount("/", StaticFiles(directory=STATIC), name="assets")
