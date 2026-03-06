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

    # SKR mint address (devnet deployment target)
    skr_mint: str = "BGUuLGTZJ7nyhReCFWpC4nQf2APE4N6dY6hizj1DXivJ"

    # Treasury wallet that receives 5% fees
    treasury_wallet: str = ""

    # Deployed betting program ID (devnet current)
    betting_program_id: str = "CoTfhg7a9vjZMCCuvpxmnhSj9CzTAahxUvDutzZjRrth"

    # Contract fee basis points used for DB payout mirroring when config fetch fails.
    contract_fee_bps_default: int = 500

    # Number of status polls when waiting for transaction confirmation.
    solana_confirm_retries: int = 15

    # ── Dev-only local signer bypass (for backend-only integration tests) ────
    # When true, /api/bets and /api/bets/{id}/claim can sign with local keypairs
    # instead of Privy.
    dev_local_signer_bypass: bool = False
    dev_local_signer_keys_dir: str = "test-users"
    dev_local_signer_user1_keyfile: str = "user1.json"
    dev_local_signer_user2_keyfile: str = "user2.json"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
