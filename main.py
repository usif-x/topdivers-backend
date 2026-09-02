import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import click
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import SQLAlchemyError
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from app.core.cache import init_cache
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.init_superadmin import create_super_admin
from app.core.limiter import limiter
from app.core.limiter_middleware import CustomLimiterMiddleware
from app.core.telegram import test_telegram_connection
from app.models import *
from app.routes.all import routes

# ============================================================================
# Directory Setup
# ============================================================================
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"

# Allow storage path to be overridden via environment variable for Docker volumes
STORAGE_PATH = os.getenv("STORAGE_PATH", str(BASE_DIR / "storage"))
STORAGE_DIR = Path(STORAGE_PATH)

# Create logs and storage folders if not exist
for directory in [LOGS_DIR, STORAGE_DIR]:
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)


def _is_heroku() -> bool:
    return bool(os.getenv("DYNO"))


# ============================================================================
# Logging Configuration
# ============================================================================
def setup_logging():
    """Configure logging for the application."""
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO
    log_format = "%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] %(message)s"

    handlers = [logging.StreamHandler(sys.stdout)]

    if not _is_heroku():
        handlers.append(
            logging.FileHandler(
                LOGS_DIR / "app.log",
                mode="a",
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=handlers,
        force=True,  # Override any existing configuration
    )

    # Suppress verbose third-party logs
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return logging.getLogger(__name__)


logger = setup_logging()


logger = setup_logging()


# ============================================================================
# Application Lifespan
# ============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    logger.info("=" * 80)
    logger.info("Starting topdivers Backend Application...")
    logger.info("=" * 80)

    # Startup
    scheduler = None
    try:
        # Initialize cache
        logger.info("Initializing cache...")
        await init_cache()
        logger.info("✓ Cache initialized successfully")

        # Initialize database
        logger.info("Initializing database...")
        Base.metadata.create_all(bind=engine)
        logger.info("✓ Database tables created successfully")

        # Start background scheduler
        logger.info("Starting background scheduler...")
        from app.core.scheduler import start_scheduler

        scheduler = start_scheduler()
        logger.info("✓ Background scheduler started")

        # Test Telegram bot configuration
        logger.info("Testing Telegram bot configuration...")
        if test_telegram_connection():
            logger.info("✓ Telegram bot is configured correctly")
        else:
            logger.warning(
                "⚠️  Telegram bot configuration issue - notifications may not work"
            )

        # Initialize S3/MinIO storage
        logger.info("Initializing S3/MinIO storage...")
        try:
            from app.utils.storage import get_s3_client

            get_s3_client()
            logger.info(
                f"S3 storage ready: endpoint={settings.S3_ENDPOINT_URL}, "
                f"bucket={settings.S3_BUCKET_NAME}"
            )
        except Exception as e:
            logger.error(f"S3/MinIO initialization failed: {e}")
            raise

        logger.info("Application startup completed successfully")

    except Exception as e:
        logger.error(f"Failed during startup: {e}", exc_info=True)
        raise

    yield  # Application is running

    # Shutdown
    logger.info("=" * 80)
    logger.info("Shutting down application...")
    logger.info("=" * 80)

    # Shutdown scheduler
    if scheduler:
        logger.info("Shutting down scheduler...")
        scheduler.shutdown()
        logger.info("✓ Scheduler shutdown completed")

    logger.info("✓ Application shutdown completed")


Base.metadata.create_all(bind=engine)


# ============================================================================
# FastAPI Application
# ============================================================================
# Disable docs in production
if settings.ENVIRONMENT == "production":
    app = FastAPI(
        lifespan=lifespan,
        title=settings.APP_NAME,
        description=settings.APP_DESCRIPTION,
        version=settings.APP_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
else:
    app = FastAPI(
        lifespan=lifespan,
        title=settings.APP_NAME,
        description=settings.APP_DESCRIPTION,
        version=settings.APP_VERSION,
    )

templates = Jinja2Templates(directory="app/templates")

# ============================================================================
# Middleware Configuration
# ============================================================================
app.state.limiter = limiter
app.add_middleware(CustomLimiterMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://topdivers.online",
        "https://admin.topdivers.online",
        "https://topdivers.hurghada-trips.online" "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.4f}"
    return response


# Request ID middleware for tracing
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(time.time()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# ============================================================================
# Exception Handlers
# ============================================================================
@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    logger.warning(f"Rate limit exceeded for {request.client.host}")
    return JSONResponse(
        status_code=HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded, please wait and try again"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation error",
            "details": exc.errors(),
        },
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.error(f"SQLAlchemy error: {type(exc).__name__}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Database error occurred",
            "type": str(type(exc).__name__),
        },
    )


# ============================================================================
# Static Files & Routes
# ============================================================================
# Mount static files (for templates/assets only; uploaded media is served from S3/MinIO)
if STORAGE_DIR.exists() and STORAGE_DIR.is_dir():
    try:
        app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")
        logger.info(f"Local static files mounted: /storage -> {STORAGE_DIR.absolute()}")
        logger.info(
            "NOTE: New uploads go to S3/MinIO. Local mount kept for legacy files only."
        )
    except Exception as e:
        logger.warning(f"Could not mount local static files (non-critical): {e}")
else:
    logger.info("No local storage directory found (uploads use S3/MinIO)")

# Include application routers
for route in routes:
    app.include_router(route)

logger.info(f"✓ Registered {len(routes)} routers")


# ============================================================================
# Health Check & Root Endpoints
# ============================================================================


logger.info(f"✓ Registered {len(routes)} routers")


# ============================================================================
# Health Check & Root Endpoints
# ============================================================================


@app.get("/", include_in_schema=False)
async def root(request: Request):
    """Root endpoint with basic application info."""
    if settings.ENVIRONMENT == "production":
        return templates.TemplateResponse("forbidden.html", {"request": request})
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/document", include_in_schema=False)
async def redoc():
    """Redirect to documentation."""
    return RedirectResponse(url="/docs")


@app.get("/documentation", include_in_schema=False)
async def docs():
    """Redirect to documentation."""
    return RedirectResponse(url="/docs")


@app.get("/health", include_in_schema=False)
async def health():
    """Detailed health check endpoint."""
    try:
        # Test database connection
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Health check database test failed: {e}")
        db_status = "unhealthy"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": datetime.now(),
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "database": db_status,
        "storage": "healthy" if STORAGE_DIR.exists() else "unhealthy",
    }


# ============================================================================
# CLI Commands
# ============================================================================
@click.group()
def cli():
    """topdivers Backend - FastAPI application management CLI."""
    pass


@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind the server to")
@click.option("--port", default=8000, help="Port to run the server on")
@click.option("--reload", is_flag=True, help="Enable auto-reload (development only)")
def dev(host: str, port: int, reload: bool):
    """Run development server with Uvicorn."""
    logger.info("=" * 80)
    logger.info("Starting development server...")
    logger.info(f"  - Host: {host}")
    logger.info(f"  - Port: {port}")
    logger.info(f"  - Reload: {reload}")
    logger.info("=" * 80)

    # Initialize superadmin
    create_super_admin()

    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="debug" if settings.DEBUG else "info",
        access_log=True,
    )


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind the server to")
@click.option("--port", default=8000, help="Port to run the server on")
@click.option("--workers", default=2, help="Number of worker processes")
def prod(host: str, port: int, workers: int):
    port = int(os.getenv("PORT", 8000))
    """Run production server with Gunicorn."""
    import subprocess

    logger.info("=" * 80)
    logger.info("Starting production server with Gunicorn...")
    logger.info(f"  - Host: {host}")
    logger.info(f"  - Port: {port}")
    logger.info(f"  - Workers: {workers}")
    logger.info("=" * 80)

    # Initialize superadmin before starting server
    create_super_admin()

    cmd = [
        "gunicorn",
        "main:app",
        "-c",
        "gunicorn.conf.py",
        "--workers",
        str(workers),
        "--bind",
        f"{host}:{port}",
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Gunicorn failed to start: {e}")
        raise click.ClickException(str(e))
    except FileNotFoundError:
        logger.error("Gunicorn not found. Install it with: pip install gunicorn")
        raise click.ClickException("Gunicorn not installed")


@cli.command()
def info():
    """Display application information."""
    click.echo("=" * 80)
    click.echo(f"Application: {settings.APP_NAME}")
    click.echo(f"Version: {settings.APP_VERSION}")
    click.echo(f"Environment: {settings.ENVIRONMENT}")
    click.echo(f"Debug Mode: {settings.DEBUG}")
    click.echo(f"Storage Directory: {STORAGE_DIR.absolute()}")
    click.echo(f"Logs Directory: {LOGS_DIR.absolute()}")
    click.echo("=" * 80)


if __name__ == "__main__":
    cli()
