# N64 (Mupen64Plus) local workflow

This repo boots the ROM in the root folder with `mupen64plus` and stores emulator data locally in `.m64p/`.

## Install (macOS/Homebrew)

```bash
brew install mupen64plus
```

## Run the ROM

```bash
./scripts/m64p-mk4.sh
```

## Run with the reverse-engineering safe hotkey profile (isolated instance)

```bash
M64P_INSTANCE_ID=reverse-a M64P_PROFILE_NAME=reverse_human ./scripts/m64p-mk4.sh
```

This applies a tracked hotkey profile to the instance-local config only (does not overwrite `.m64p/config/mupen64plus.cfg`).

## Save / Load states while playing

- `0`-`9`: choose save slot
- `F5`: save state to current slot
- `F7`: load state from current slot
- `Esc`: quit

On many Mac keyboards, use `fn` + `F5` / `fn` + `F7` unless function keys are configured as standard keys.

## Start from the newest savestate file

```bash
./scripts/m64p-load-latest-state.sh
```

This script picks the newest file in `.m64p/data/savestates/` and starts the emulator with `--savestate`.
