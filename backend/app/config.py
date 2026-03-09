from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://imk:imk_dev_password@localhost:5432/immortalkombat"
    privy_app_id: str = "cmm5ifxpw00p50cl5bkx86zcd"
    privy_client_id: str = "client-WY6WiWvWLFu17WpAsHYy8EuUWhdVFdGhA3vKCeeAFnZ3s"
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
    contract_min_bet_base_units_default: int = 100
    contract_max_bet_base_units_default: int = 400
    token_symbol: str = "SKR"
    token_decimals: int = 6
    explorer_base_url: str = "https://solscan.io"

    # Number of status polls when waiting for transaction confirmation.
    solana_confirm_retries: int = 15

    # ── Dev-only local signer bypass (for backend-only integration tests) ────
    # When true, /api/bets and /api/bets/{id}/claim can sign with local keypairs
    # instead of Privy.
    dev_local_signer_bypass: bool = False
    dev_local_signer_keys_dir: str = "test-users"
    dev_local_signer_user1_keyfile: str = "user1.json"
    dev_local_signer_user2_keyfile: str = "user2.json"

    # ── Automated loop queue (4-fighter directed cycle) ──────────────────────
    auto_queue_enabled: bool = True
    queue_tick_seconds: float = 1.0
    queue_match_countdown_seconds: int = 60
    queue_lock_before_start_seconds: int = 1
    queue_leader_lock_file: str = "/tmp/imk_queue_loop.lock"

    # ── WebRTC streaming (replaces HLS when enabled) ──────────────────────────
    # Set use_webrtc=true in .env to enable. HLS still works as fallback.
    use_webrtc: bool = False
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "imk_key"
    livekit_api_secret: str = "imk_secret_change_in_production_32chars"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
