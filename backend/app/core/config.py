from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://chemflow:chemflow@localhost:5432/chemflow"
    app_name: str = "ChemFlow"
    env: str = "development"
    debug: bool = False

    # JWT
    secret_key: str = "CHANGE-ME-IN-PRODUCTION-USE-A-LONG-RANDOM-STRING"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 h

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
