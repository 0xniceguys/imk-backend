# Key Management (Local)

This directory stores local Solana keypairs used by deployment tooling.

Generated files (default):

- `devnet-deployer.json`
- `devnet-admin.json`
- `devnet-treasury.json`
- `devnet-pubkeys.env`

## Safety

- Keep this directory private.
- Do not commit keypair JSON files.
- Permissions are set to `700` for directory and `600` for key files.

## Regenerate

```bash
bash contracts/deployment/devnet/scripts/01_generate_keys.sh
```

Force regeneration:

```bash
FORCE=1 bash contracts/deployment/devnet/scripts/01_generate_keys.sh
```
