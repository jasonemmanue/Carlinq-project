from fastapi import APIRouter

from app.api.v1.routes import admin, auth, disputes, drivers, files, goals, rides, users, wallet, zones

api_router = APIRouter(prefix="/api/v1")
for module in (auth, users, drivers, goals, rides, wallet, zones, disputes, files, admin):
    api_router.include_router(module.router)
