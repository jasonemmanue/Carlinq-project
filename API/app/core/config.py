from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration d'infrastructure (.env).

    Les regles metier modifiables a chaud (tarifs, objectifs, partage d'objectif)
    ne sont PAS ici : elles vivent en base (table `app_settings`) et se modifient
    via `/api/v1/admin/settings/*`. Les valeurs ci-dessous ne servent que de
    valeurs initiales lors du premier demarrage.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Carlinq API"
    APP_ENV: str = "development"

    SECRET_KEY: str = "change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:55432/carlinq"

    # Cache Redis (vide = cache desactive). Jamais source de verite.
    REDIS_URL: str = "redis://localhost:56379/0"
    CACHE_TTL_SECONDS: int = 300

    # Fuseau metier : les semaines d'objectifs et les quotas journaliers
    # se calculent a l'heure de Douala.
    TIMEZONE: str = "Africa/Douala"

    # Liste exacte d'origines web, separees par des virgules. Vide : aucun site tiers
    # (les apps mobiles et le serveur du backoffice n'utilisent pas CORS).
    CORS_ORIGINS: str = ""

    # Recharge, retrait et Mobile Money sans operateur reel : developpement et tests
    # uniquement. Faux par defaut : aucun argent ne peut etre cree sans paiement.
    PAYMENTS_SIMULATED: bool = False

    # Connexion : blocage apres trop d'echecs (par numero, et par adresse IP).
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCK_SECONDS: int = 120
    LOGIN_MAX_FAILURES_PER_IP: int = 50

    @field_validator("DATABASE_URL")
    @classmethod
    def _psycopg2_driver(cls, v: str) -> str:
        # Railway / Heroku fournissent postgres:// ou postgresql:// : on force le driver psycopg2.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg2://" + v[len(prefix):]
        return v

    @model_validator(mode="after")
    def _no_default_secret(self):
        if self.APP_ENV not in ("development", "test") and self.SECRET_KEY.startswith("change-me"):
            raise ValueError("SECRET_KEY doit etre defini hors developpement")
        return self


settings = Settings()
