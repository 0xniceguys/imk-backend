from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://imk:imk_dev_password@localhost:5432/immortalkombat"
    privy_app_id: str = "cmm5ifxpw00p50cl5bkx86zcd"
    privy_app_secret: str = ""
    hls_output_dir: str = "./hls_output"
    vod_archive_dir: str = "./vod_archive"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
