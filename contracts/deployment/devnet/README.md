# Devnet Deployment Workspace

This folder contains non-destructive deployment tooling for the Solana program in:

- `contracts/skr_parimutuel_betting`

No script here auto-runs deployment. Deployment script requires explicit confirmation.

## Structure

- `.env.devnet.example` - required env template
- `scripts/00_check_prereqs.sh` - verify required tools
- `scripts/01_generate_keys.sh` - create deployer/admin/treasury keypairs in `contracts/keys`
- `scripts/02_show_pubkeys.sh` - print key pubkeys and export file
- `scripts/04_scaffold_env.sh` - create `.env.devnet` from template and prefill treasury wallet
- `scripts/03_request_airdrop.sh` - request devnet SOL for keypairs
- `scripts/10_build.sh` - build Anchor program
- `scripts/12_prepare_program_id.sh` - sync program id into Rust + Anchor.toml
- `scripts/20_deploy_devnet.sh` - deploy to devnet (guarded)
- `scripts/30_init_config.sh` + `30_init_config.py` - call `init_config` (raw RPC, no IDL needed)
- `scripts/31_read_config.sh` + `31_read_config.py` - fetch and decode config account
- `scripts/32_create_treasury_ata.sh` - create treasury ATA for SKR mint
- `scripts/40_verify_deploy.sh` - readonly RPC checks

## Quick Start

1. Copy env file:

```bash
cp contracts/deployment/devnet/.env.devnet.example contracts/deployment/devnet/.env.devnet
```

2. Generate keys:

```bash
bash contracts/deployment/devnet/scripts/01_generate_keys.sh
```

3. Fill `.env.devnet` values:

- `SKR_MINT`
- `MIN_BET_BASE_UNITS`
- `MAX_BET_BASE_UNITS`
- `BETTING_PROGRAM_ID` (after deploy)

4. Run prereq check:

```bash
bash contracts/deployment/devnet/scripts/00_check_prereqs.sh
```

5. Build/deploy/init only when ready.

## Safety

`20_deploy_devnet.sh` will refuse to deploy unless:

- `DEPLOY_CONFIRM=YES`
