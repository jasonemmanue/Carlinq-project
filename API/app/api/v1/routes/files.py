"""Lecture des fichiers envoyes : proprietaire, participants de la course ou admin."""
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.errors import Forbidden, NotFound
from app.db.session import get_db
from app.models import User, UserRole
from app.services import rides as rides_svc
from app.services import storage

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{key:path}", summary="Telecharger un fichier (acces controle)")
def download(key: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    parts = key.split("/")
    if len(parts) != 3 or not parts[1].isdigit() or parts[0] not in ("documents", "messages"):
        raise NotFound("Fichier")
    folder, owner = parts[0], int(parts[1])
    if user.role != UserRole.admin:
        if folder == "documents" and not (user.driver and user.driver.id == owner):
            raise Forbidden("FILE_NOT_ACCESSIBLE", "Fichier non accessible")
        if folder == "messages":
            rides_svc.assert_participant(rides_svc.get_ride(db, owner), user)
    path = storage.resolve(key)
    return FileResponse(path, media_type=storage.CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                        headers={"Cache-Control": "private, no-store"})
