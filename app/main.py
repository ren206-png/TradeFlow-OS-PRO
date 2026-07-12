import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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
    # Seed system intake templates on every startup (idempotent)
    if settings.intake_flows_v2:
        try:
            from app.database import async_session_factory
            from app.services.intake import seed_system_templates
            async with async_session_factory() as _seed_db:
                await seed_system_templates(_seed_db)
                await _seed_db.commit()
            logger.info("Intake templates seeded.")
        except Exception as _seed_err:
            logger.warning("Intake template seeding failed: %s", _seed_err)
    yield
    shutdown_scheduler()
    logger.info("TradeFlow-OS Pro shutting down.")


app = FastAPI(
    title="TradeFlow-OS Pro API",
    version="1.0.0",
    description="Multi-tenant voice AI platform for trade contractors.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

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

_VISITOR_COOKIE = "tf_visitor"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request):
    visitor_token = request.cookies.get(_VISITOR_COOKIE)
    if not visitor_token:
        import uuid as _uuid
        visitor_token = str(_uuid.uuid4())

    ab_variant = "A" if hash(visitor_token) % 2 == 0 else "B"

    response = templates.TemplateResponse("landing.html", {
        "request": request,
        "demo_phone": settings.demo_phone_number or None,
        "ff_trust_v2": settings.trust_v2,
        "ff_mobile_hero_v2": settings.mobile_hero_v2,
        "ff_live_metrics": settings.live_metrics,
        "ab_variant": ab_variant,
    })
    if not request.cookies.get(_VISITOR_COOKIE):
        response.set_cookie(_VISITOR_COOKIE, visitor_token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return response


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


_ALLOWED_EVENTS = {
    "page_view", "cta_hero_click", "cta_sticky_click", "demo_call_click",
    "exit_modal_shown", "exit_modal_cta_click",
    "scroll_25", "scroll_50", "scroll_75", "scroll_100",
    "signup_start", "signup_complete",
}

@app.post("/api/events", tags=["public"], status_code=204)
async def track_event(request: Request):
    """
    Lightweight analytics event ingestion.
    Accepts JSON: {event, session_id, page, referrer}
    Rate-limited, allowlist-validated, fire-and-forget (never blocks the client).
    """
    from app.utils.rate_limit import check_rate_limit
    allowed, _ = check_rate_limit(request, "track_event", max_requests=60, window_seconds=60)
    if not allowed:
        return JSONResponse(status_code=429, content={"error": "rate limited"})

    try:
        body = await request.json()
    except Exception:
        return  # silently drop malformed payloads

    event_name = str(body.get("event", ""))[:64]
    if event_name not in _ALLOWED_EVENTS:
        return  # silently drop unknown events

    session_id = str(body.get("session_id", ""))[:64]
    page = str(body.get("page", "/"))[:255]
    referrer = str(body.get("referrer", ""))[:512]
    ab_variant_raw = body.get("ab_variant")
    ab_variant = str(ab_variant_raw)[:2] if ab_variant_raw in ("A", "B") else None

    # Coarse device detection from UA header
    ua = request.headers.get("user-agent", "").lower()
    device = "mobile" if any(x in ua for x in ("mobile", "android", "iphone")) else \
             "tablet" if "ipad" in ua else "desktop"

    try:
        from app.models.page_event import PageEvent
        from app.database import get_db as _get_db
        async for db in _get_db():
            db.add(PageEvent(
                event_name=event_name,
                session_id=session_id,
                page=page,
                referrer=referrer,
                device=device,
                ab_variant=ab_variant,
            ))
            await db.commit()
            break
    except Exception as exc:
        logger.debug("track_event DB error (non-fatal): %s", exc)


@app.get("/api/public/metrics", tags=["public"])
async def public_metrics():
    """
    Live DB-backed stats for the landing page.
    Cached for 5 minutes to avoid hammering the DB on every page load.
    Only served when settings.live_metrics is True; otherwise returns placeholders.
    Rate-limited at the nginx/Railway level (no per-IP logic needed here).
    """
    if not settings.live_metrics:
        # Placeholder values — honest about being estimates
        return JSONResponse({
            "total_calls_handled": None,
            "total_appointments_booked": None,
            "contractors_active": None,
            "live": False,
        })

    # Cache key is just time-bucketed to 5-minute windows
    bucket = int(time.time()) // 300

    @lru_cache(maxsize=1)
    def _cache_key(b: int):
        return b  # forces lru_cache to re-run when bucket changes

    _cache_key(bucket)  # advance bucket

    try:
        from app.database import get_db as _get_db
        from app.models.call import CallSession
        from app.models.lead import Lead
        from app.models.contractor import Contractor
        from sqlalchemy import func, select as sa_select

        async for db in _get_db():
            total_calls = (await db.execute(sa_select(func.count()).select_from(CallSession))).scalar() or 0
            booked = (await db.execute(
                sa_select(func.count()).select_from(Lead).where(Lead.appointment_status == "booked")
            )).scalar() or 0
            active_contractors = (await db.execute(
                sa_select(func.count()).select_from(Contractor).where(Contractor.is_active == True)
            )).scalar() or 0
            break

        return JSONResponse({
            "total_calls_handled": int(total_calls),
            "total_appointments_booked": int(booked),
            "contractors_active": int(active_contractors),
            "live": True,
            "bucket": bucket,
        })
    except Exception as exc:
        logger.warning("public_metrics DB error: %s", exc)
        return JSONResponse({"live": False, "error": "metrics unavailable"}, status_code=503)


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
