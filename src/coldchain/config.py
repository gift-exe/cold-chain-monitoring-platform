"""Settings shared by all services, read from the environment or the .env file."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    coldchain_db_password: str = "dev-coldchain-pw"
    coldchain_app_password: str = "dev-app-pw"

    keycloak_url: str = "http://localhost:8080"          # where the services reach Keycloak
    keycloak_public_url: str = "http://localhost:8080"   # the issuer named in tokens (KC_HOSTNAME)
    keycloak_realm: str = "coldchain"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    kafka_bootstrap: str = "localhost:9092"
    # Must stay the same across restarts: the broker keeps queued messages for this client's session
    bridge_client_id: str = "ingestion-bridge-1"

    detection_sweep_s: float = 15.0          # how often device silence is checked
    model_dir: str = "models"                # trained anomaly models (detection/anomaly.py)

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "incidents"
    audit_anchor_dir: str = "anchors"        # outside the database (Section 3.4.6)

    def db_url(self, role: str = "app") -> str:
        """SQLAlchemy URL. role="owner" for migrations, "app" for the services."""
        user, password = {
            "owner": ("coldchain_owner", self.coldchain_db_password),
            "app": ("coldchain_app", self.coldchain_app_password),
        }[role]
        return f"postgresql+psycopg://{user}:{password}@{self.postgres_host}:{self.postgres_port}/coldchain"


@lru_cache
def get_settings() -> Settings:
    return Settings()
