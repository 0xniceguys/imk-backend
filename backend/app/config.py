from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://imk:imk_dev_password@localhost:5432/immortalkombat"
    privy_app_id: str = "cmm5ifxpw00p50cl5bkx86zcd"
    privy_app_secret: str = ""
    use_devnet: bool = True  # default devnet for contract integration
    hls_output_dir: str = "./hls_output"
    vod_archive_dir: str = "./vod_archive"
    host: str = "0.0.0.0"
    port: int = 8000
    redis_url: str = "redis://localhost:6379"

    # ── Solana / Contract ─────────────────────────────────────────────────────
    # Admin keypair as base58-encoded private key bytes (88 chars).
    # Generate with: solana-keygen new --outfile /tmp/admin.json && cat /tmp/admin.json
    admin_keypair_b58: str = ""

    # SKR mint address — use devnet standin (USDC) during development
    skr_mint: str = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

    # Treasury wallet that receives 5% fees
    treasury_wallet: str = ""

    # Deployed betting program ID (update when deploying to devnet/mainnet)
    betting_program_id: str = "7woZnJL2FL4yG44EEDgVtY3YX6TqGFF1yuWND4tiDuAv"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
