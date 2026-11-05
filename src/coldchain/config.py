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
    # Evaluation replay only (Section 3.2.7): days of simulated readings are replayed in minutes, so receipt
    # times mean nothing. The late flag (FR2.3) and the device-silence sweep, which both depend on receipt
    # time, are switched off there and tested on runs whose receipt times are real.
    late_horizon_h: float = 72.0             # 0 disables the late flag
    silence_sweep: bool = True
    model_dir: str = "models"                # trained anomaly models (detection/anomaly.py)

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "incidents"
    audit_anchor_dir: str = "anchors"        # outside the database (Section 3.4.6)

    # SMS gateway: Africa's Talking's API, or the local emulator that speaks the same protocol
    # (simulator/sms_gateway.py). For the Africa's Talking sandbox: AT_API_URL=https://api.sandbox.africastalking.com,
    # AT_USERNAME=sandbox and the sandbox API key.
    at_api_url: str = "http://localhost:8002"
    at_username: str = "sandbox"
    at_api_key: str = "dev-at-key"
    at_sender_id: str | None = None          # alphanumeric sender id, if one is registered
    sms_redirect_to: str | None = None       # development only: every SMS goes to this one phone instead
    sms_trust_sent: bool = False             # development only: a message the gateway accepts counts as delivered
                                             # (the Africa's Talking sandbox's delivery reports are unreliable)
    sms_callback_token: str = "dev-callback-token"   # shared secret in the gateway's callback URLs
    code_secret: str = "dev-code-secret"     # HMAC key for response codes; never stored in the database
    digest_hour_utc: int = 6                 # retrospective digests at 07:00 WAT

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
