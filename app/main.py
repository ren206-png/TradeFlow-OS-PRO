import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.routers import auth, billing, contractor_app, contractors, dashboard, leads, onboarding, portal, retell, twilio_sms
from app.services.scheduler import shutdown_scheduler, start_scheduler
from app.utils.logging import configure_logging

configure_logging(debug=settings.debug)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("TradeFlow-OS Pro starting up...")
    await init_db()
    start_scheduler()
    logger.info("Database initialised.")
    yield
    shutdown_scheduler()
    logger.info("TradeFlow-OS Pro shutting down.")


app = FastAPI(
    title="TradeFlow-OS Pro API",
    version="1.0.0",
    description="Multi-tenant voice AI platform for trade contractors.",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000)
    logger.info(
        "%s %s %d %dms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(portal.router)
app.include_router(contractor_app.router)
app.include_router(retell.router)
app.include_router(contractors.router)
app.include_router(leads.router)
app.include_router(dashboard.router)
app.include_router(onboarding.router)
app.include_router(billing.router)
app.include_router(twilio_sms.router)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request):
    return templates.TemplateResponse("landing.html", {
        "request": request,
        "demo_phone": settings.demo_phone_number or None,
    })


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt():
    return """User-agent: *
Allow: /
Allow: /auth/signup
Allow: /auth/login
Disallow: /dashboard
Disallow: /portal
Disallow: /api
Disallow: /retell
Disallow: /health

Sitemap: https://tradesflowos.com/sitemap.xml
"""


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml():
    from fastapi.responses import Response
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://tradesflowos.com/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://tradesflowos.com/auth/signup</loc>
    <changefreq>monthly</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://tradesflowos.com/auth/login</loc>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
    return Response(content=content, media_type="application/xml")


@app.get("/signup", include_in_schema=False)
async def signup_redirect():
    return RedirectResponse(url="/auth/signup")


@app.get("/login", include_in_schema=False)
async def login_redirect():
    return RedirectResponse(url="/auth/login")


@app.get("/demo/number", tags=["demo"])
async def demo_number():
    """Public endpoint — returns the demo phone number for the marketing site hero."""
    if not settings.demo_phone_number:
        raise HTTPException(status_code=503, detail="Demo line not yet provisioned.")
    return {"phone_number": settings.demo_phone_number}


@app.get("/health", tags=["health"])
async def health():
    from app.database import get_db as _get_db
    db_ok = False
    try:
        async for db in _get_db():
            from sqlalchemy import text
            await db.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    return JSONResponse(
        status_code=200 if db_ok else 503,
        content={
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "error",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "version": "1.0.0",
        },
    )


# ---------------------------------------------------------------------------
# Global exception handler (catches any unhandled exception and returns JSON)
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc), "success": False},
    )
