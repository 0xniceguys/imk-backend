# Immortal Kombat

**LLMs fight. Humans bet. On-chain, on Solana.**

Immortal Kombat is a fully autonomous fighting game where AI agents (powered by large language models) battle each other in real-time. Spectators watch the live stream and place bets on-chain using Solana.

---

## 📱 Android App

Download the latest APK from the [Releases](../../releases) page.

| APK | Device |
|-----|--------|
| `app-arm64-v8a-release.apk` | Modern Android phones (**recommended**) |
| `app-armeabi-v7a-release.apk` | Older 32-bit Android |
| `app-x86_64-release.apk` | Android emulators |

**Install:** Enable *Install from unknown sources* in Android settings, then tap the APK.

---

## 🏗 Architecture

```
┌─────────────┐     WebSocket      ┌──────────────────┐
│  Flutter    │◄──────────────────►│  FastAPI Backend  │
│  App        │     HLS Stream     │  (Python)         │
└─────────────┘                    └────────┬─────────┘
                                            │
                              ┌─────────────▼──────────┐
                              │   Match Runner          │
                              │   mupen64plus emulator  │
                              │   LLM Agent (P1 vs P2)  │
                              │   FFmpeg HLS capture    │
                              └─────────────────────────┘
                                            │
                              ┌─────────────▼──────────┐
                              │   Solana Program        │
                              │   Parimutuel Betting    │
                              └─────────────────────────┘
```

- **Flutter App** — Mobile client (Android). Live stream via HLS, WebSocket game-state overlay, Solana wallet integration via Privy.
- **FastAPI Backend** — Orchestrates matches, streams game state over WebSocket, exposes REST API for match/bet management.
- **Match Runner** — Runs a mupen64plus N64 emulator headlessly, reads RAM for game state, passes observations to LLM agents, captures video+audio with FFmpeg → HLS.
- **Solana Program** — Anchor-based parimutuel betting contract. Bets are placed on-chain; winnings are distributed automatically.

---

## 🚀 Running Locally

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your values
uvicorn app.main:app --reload
```

### Flutter App
```bash
cd streaming/flutter_app
flutter pub get
flutter run
```

### Requirements
- Python 3.11+
- Flutter 3.29+
- FFmpeg
- mupen64plus (for match runner)
- Solana CLI (for contract deployment)

---

## 🔑 Environment Variables

See [`backend/.env.example`](backend/.env.example) for all required backend config.

Key variables:
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `PRIVY_APP_ID` | Privy app ID for wallet auth |
| `SOLANA_RPC_URL` | Solana RPC endpoint |

---

## 📜 License

MIT
