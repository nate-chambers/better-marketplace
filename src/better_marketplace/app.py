import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .account import AccountUsage
from .identity import CATALOG
from .jobs import Collector
from .models import MAKES, Query, RunRequest, Search, default_search
from .service import Listings, export_csv
from .settings import Settings
from .store import Store, digest

STATIC = Path(__file__).with_name("static")


class Bookmark(BaseModel):
    saved: bool


class ImportRequest(BaseModel):
    records: list[dict] = Field(max_length=10000)
    observed_at: str | None = None


def create_app(settings=None):
    settings = settings or Settings.from_env()
    store = Store(settings.data_dir)
    listings = Listings(store)
    collector = Collector(store, settings)
    account_usage = AccountUsage()

    @asynccontextmanager
    async def lifespan(app):
        yield
        collector.close()

    app = FastAPI(
        title="Better Marketplace",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.store, app.state.collector = store, collector
    hosts = {urlparse(o).hostname for o in settings.allowed_origins} | {
        "localhost",
        "127.0.0.1",
        "testserver",
    }
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(h for h in hosts if h))

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") not in settings.allowed_origins:
                return JSONResponse(
                    {
                        "detail": "This browser origin is not allowed. Set MARKETPLACE_ALLOWED_ORIGINS for your private URL."
                    },
                    status_code=403,
                )
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site writes are disabled"}, status_code=403)
            if request.headers.get("x-marketplace-client") != "1":
                return JSONResponse(
                    {"detail": "Missing X-Marketplace-Client header"}, status_code=403
                )
            # Bound both chunked and Content-Length request bodies before parsing JSON.
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    return JSONResponse(
                        {"detail": "Import exceeds 20 MB; split the export"}, status_code=413
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/docs", include_in_schema=False)
    def api_docs():
        nonce = secrets.token_urlsafe(24)
        page = get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="Better Marketplace API",
            swagger_favicon_url="/static/favicon.svg",
        ).body.decode()
        page = page.replace("<script>", f'<script nonce="{nonce}">')
        page = page.replace(
            "const ui = SwaggerUIBundle({",
            """const ui = SwaggerUIBundle({
            requestInterceptor: (request) => {
                request.headers['X-Marketplace-Client'] = '1';
                return request;
            },""",
        )
        return Response(
            page,
            media_type="text/html",
            headers={
                "Content-Security-Policy": (
                    "default-src 'self'; img-src 'self' data:; "
                    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                    f"script-src 'self' https://cdn.jsdelivr.net 'nonce-{nonce}'; "
                    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
                ),
            },
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/apify/usage")
    def apify_usage(refresh: bool = False):
        return account_usage.get(refresh=refresh)

    @app.get("/api/bootstrap")
    def bootstrap():
        return {
            "version": __version__,
            "default_search": default_search().model_dump(),
            "searches": [
                {**saved, "search": Search.model_validate(saved["search"]).model_dump()}
                for saved in store.searches()
            ],
            "makes": MAKES,
            "models": CATALOG,
            "token_configured": bool(os.getenv("APIFY_TOKEN")),
            "limits": {
                "max_run_usd": settings.max_run_usd,
                "max_job_usd": settings.max_job_usd,
                "max_jobs": settings.max_jobs,
            },
            "jobs": store.jobs(),
            "observation_count": store.revision(),
        }

    @app.post("/api/listings/query")
    def query(body: Query):
        return listings.query(body)

    @app.post("/api/listings/export/{format}")
    def export(format: str, body: Query):
        if format not in {"csv", "json"}:
            raise HTTPException(404, "Choose csv or json")
        items = listings.query(body, all_pages=True)["items"]
        content = export_csv(items) if format == "csv" else json.dumps(items, indent=2)
        return Response(
            content,
            media_type="text/csv" if format == "csv" else "application/json",
            headers={
                "Content-Disposition": f'attachment; filename="marketplace-listings.{format}"'
            },
        )

    @app.post("/api/listings/import")
    def import_listings(body: ImportRequest):
        return {
            "imported": store.import_records(
                body.records, "import:" + digest(body.records), body.observed_at
            )
        }

    @app.put("/api/listings/{identifier}/bookmark")
    def bookmark(identifier: str, body: Bookmark):
        if not any(row["listing_id"] == identifier for row in store.observations()):
            raise HTTPException(404, "Listing not found")
        store.bookmark(identifier, body.saved)
        return {"saved": body.saved}

    @app.post("/api/searches")
    def save_search(body: Search):
        identifier = uuid.uuid4().hex
        store.save_search(identifier, body.model_dump())
        return {"id": identifier, "search": body.model_dump()}

    @app.put("/api/searches/{identifier}")
    def update_search(identifier: str, body: Search):
        if not any(s["id"] == identifier for s in store.searches()):
            raise HTTPException(404, "Saved search not found")
        store.save_search(identifier, body.model_dump())
        return {"id": identifier, "search": body.model_dump()}

    @app.delete("/api/searches/{identifier}")
    def delete_search(identifier: str):
        if not store.delete_search(identifier):
            raise HTTPException(404, "Saved search not found")
        return {"deleted": True}

    @app.post("/api/collections/plan")
    def plan(body: RunRequest):
        return collector.plan(body)

    @app.post("/api/collections", status_code=202)
    def collect(body: RunRequest):
        return collector.start(body)

    @app.get("/api/collections")
    def collections():
        return store.jobs()

    @app.post("/api/collections/{identifier}/stop")
    def stop(identifier: str):
        return collector.stop(identifier)

    @app.post("/api/collections/{identifier}/resume")
    def resume(identifier: str):
        return collector.resume(identifier)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
