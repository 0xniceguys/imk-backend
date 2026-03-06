# Deployment Testing Workspace

All deployment-related tests are organized here.

## Test Scripts

- `00_run_local_anchor_tests.sh` - run existing Anchor unit/integration tests (`tests/skr_parimutuel_betting.ts`) locally.
- `10_devnet_readonly_smoke.sh` - run readonly devnet smoke checks after deployment/config.
- `10_devnet_readonly_smoke.py` - Python checker used by the shell wrapper (raw RPC decode).
- `20_devnet_live_e2e.sh` - run live devnet E2E flow with two test users and SKR funding.
- `20_devnet_live_e2e.py` - raw-RPC instruction driver used by the shell wrapper.
- `30_devnet_full_contract_test.sh` - run comprehensive live devnet test matrix (all instruction paths).
- `30_devnet_full_contract_test.py` - full raw-RPC test matrix runner.

## Usage

Local contract tests:

```bash
bash contracts/deployment/testing/00_run_local_anchor_tests.sh
```

Devnet readonly smoke:

```bash
bash contracts/deployment/testing/10_devnet_readonly_smoke.sh
```

Live devnet E2E:

```bash
bash contracts/deployment/testing/20_devnet_live_e2e.sh
```

Comprehensive live devnet matrix:

```bash
bash contracts/deployment/testing/30_devnet_full_contract_test.sh
```

## Notes

- These scripts do not deploy anything.
- Devnet smoke requires `.env.devnet` and a deployed program id.
