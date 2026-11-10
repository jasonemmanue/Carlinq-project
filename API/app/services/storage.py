"""Fichiers envoyes par les utilisateurs, sur le volume Railway.

Railway expose le point de montage dans RAILWAY_VOLUME_MOUNT_PATH. Hors developpement,
sans volume, l'envoi est refuse : le disque du conteneur est efface a chaque deploiement.

Le type est verifie sur les octets, et le nom du fichier n'est jamais repris du client.
"""
import os
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import Invalid, NotFound, ProviderUnavailable

MAX_BYTES = 8 * 1024 * 1024
CONTENT_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".pdf": "application/pdf"}


def root() -> Path:
    configured = os.environ.get("STORAGE_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if configured:
        return Path(configured)
    if settings.APP_ENV in ("development", "test"):
        return Path("storage")
    raise ProviderUnavailable("STORAGE_UNAVAILABLE", "Stockage des fichiers indisponible")


def _extension(data: bytes, allow_pdf: bool) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if allow_pdf and data.startswith(b"%PDF-"):
        return ".pdf"
    raise Invalid("FILE_TYPE_NOT_ALLOWED", "Format de fichier non autorise")


def save(folder: str, owner_id: int, upload: UploadFile, *, allow_pdf: bool) -> str:
    """Enregistre le fichier et retourne sa cle `dossier/proprietaire/nom`."""
    data = upload.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise Invalid("FILE_TOO_LARGE", "Fichier trop volumineux (8 Mo maximum)")
    extension = _extension(data, allow_pdf)
    base = root()
    directory = base / folder / str(owner_id)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{extension}"
    (directory / name).write_bytes(data)
    return f"{folder}/{owner_id}/{name}"


def resolve(key: str) -> Path:
    """Chemin d'une cle ; rien ne peut sortir du dossier de stockage."""
    base = root().resolve()
    path = (base / key).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise NotFound("Fichier")
    return path


def url_for(key: str) -> str:
    return f"/api/v1/files/{key}"
