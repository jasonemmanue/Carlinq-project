import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError, StatementError

from app.api.v1.router import api_router
from app.core import cache
from app.core.config import settings
from app.core.errors import AppError, app_error_handler
from app.db.session import engine

log = logging.getLogger("carlinq.api")
DOCS = settings.APP_ENV != "production"

app = FastAPI(
    title=settings.APP_NAME,
    docs_url="/docs" if DOCS else None,
    redoc_url="/redoc" if DOCS else None,
    openapi_url="/openapi.json" if DOCS else None,
    version="1.1.0",
    description=(
        "API REST Carlinq v1.2 - Carlinq Flexible + Carlinq Taxi. "
        "Authentification, passagers, chauffeurs (Drivers & Copilote), courses et tarification "
        "(arrets, Pause Arret, embouteillage, route degradee), portefeuille, objectifs hebdomadaires "
        "et partage d'objectif entre chauffeurs, zones Carlinq Taxi, litiges, administration."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    # Authentification par jeton Bearer : aucun cookie a autoriser.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": {}}})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if settings.APP_ENV != "development":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Meme format que FastAPI, sans jamais renvoyer la valeur saisie (mot de passe...).
    return JSONResponse(status_code=422, content={"detail": [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()
    ]})


@app.exception_handler(IntegrityError)
async def integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
    # Deux requetes simultanees : la contrainte d'unicite a tranche.
    log.warning("Conflit d'integrite : %s", type(exc.orig).__name__)
    return _error(409, "CONFLICT", "Operation deja effectuee ou en conflit")


@app.exception_handler(DataError)
async def data_error(_: Request, exc: DataError) -> JSONResponse:
    return _error(422, "INVALID_INPUT", "Donnees invalides")


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError) -> JSONResponse:
    # psycopg2 refuse le caractere NUL avant meme d'envoyer la requete.
    if "NUL" in str(exc):
        return _error(422, "INVALID_INPUT", "Donnees invalides")
    log.exception("Erreur interne", exc_info=exc)
    return _error(500, "INTERNAL_ERROR", "Erreur interne")


@app.exception_handler(StatementError)
async def statement_error(_: Request, exc: StatementError) -> JSONResponse:
    if isinstance(exc.orig, ValueError):  # caractere NUL...
        return _error(422, "INVALID_INPUT", "Donnees invalides")
    log.exception("Erreur SQL", exc_info=exc)
    return _error(500, "INTERNAL_ERROR", "Erreur interne")


@app.get("/", tags=["root"])
def root():
    return {"app": settings.APP_NAME, "env": settings.APP_ENV, "docs": "/docs"}


@app.get("/healthz", tags=["root"])
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "cache": cache.ping()}


app.include_router(api_router)
