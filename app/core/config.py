from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Paribus API"
    APP_VERSION: str = "0.1.0"
    APP_DESCRIPTION: str = "FastAPI boilerplate for Paribus assignment"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # API
    API_V1_PREFIX: str = "/api/v1"

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # CORS
    ALLOWED_ORIGINS: list[str] = ["*"]

    # Hospital Directory API (given)
    HOSPITAL_API_BASE_URL: str = "https://hospital-directory.onrender.com"
    HOSPITAL_API_TIMEOUT: float = 30.0
    HOSPITAL_API_CONCURRENCY: int = 5
    HOSPITAL_API_MAX_RETRIES: int = 2
    BULK_MAX_HOSPITALS: int = 20

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
