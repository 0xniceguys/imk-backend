# IMK Agent & Fighter System - Comprehensive Analysis

## Executive Summary

This document analyzes the current agent/fighter system across Flutter app and backend, identifies hardcoded data, documents bugs, and proposes a systematic implementation plan for complete CRUD functionality with agent training upload capabilities.

---

## 1. Current Architecture

### 1.1 Backend Agent System

**Location:** `/home/ubuntu/imk/backend/app/agents/`

**Components:**
- `base.py` - Abstract base classes (`FighterAgent`, `AgentInfo`)
- `__init__.py` - Agent registry and discovery system
- `random_agent.py` - Weighted random action agent (builtin)
- `cpu_agent.py` - Neutral/CPU agent (builtin)
- `onnx_agent.py` - Neural network agent loader (LSTM, Transformer, etc.)
- `checkpoints/` - ONNX model files

**Available Agent Types:**

| Agent ID | Name | Architecture | Requires Checkpoint | Status |
|----------|------|--------------|---------------------|---------|
| `random` | Random | builtin | No | ✅ Available |
| `cpu` | CPU (Neutral) | builtin | No | ✅ Available |
| `lstm` | LSTM Policy | lstm | Yes | ✅ Has checkpoint |
| `obj_belief` | Object Belief | obj_belief | Yes | ✅ Has checkpoint |
| `disc_rssm` | Discrete RSSM | disc_rssm | Yes | ✅ Has checkpoint |
| `transformer` | Transformer | transformer | Yes | ✅ Has checkpoint |

**Agent Discovery Flow:**
```python
discover_agents() → List[AgentInfo]
  ├─ Scans _AGENT_DEFS
  ├─ Checks checkpoint existence for neural agents
  └─ Returns available agents

create_agent(agent_id) → FighterAgent
  ├─ Validates agent_id exists
  ├─ For builtin: instantiate directly
  └─ For neural: load ONNX checkpoint
```

### 1.2 Backend Fighter (Database Model)

**Location:** `/home/ubuntu/imk/backend/app/db/models.py`

**Fighter Schema:**
```python
class Fighter(Base):
    id: UUID (primary key)
    name: str (unique) - Display name
    slug: str (unique) - URL-safe identifier
    character: str - MK4 character name
    character_id: int - MK4 character roster ID
    llm_model: str - Associated LLM description
    image_url: str | None - Character portrait URL
    agent_checkpoint: str | None - Path to .onnx file
    agent_architecture: str | None - "lstm", "transformer", etc.
    matches_played: int
    matches_won: int
    created_at: datetime

    @property win_rate: float - Calculated from matches
```

**Key Points:**
- Fighters are database records (persistent)
- Agents are code/checkpoint files (ephemeral)
- **Disconnect:** Fighter.agent_checkpoint exists but isn't used!
- Matches reference `p1_agent` and `p2_agent` as strings ("random", "lstm")

### 1.3 Flutter Fighter Model

**Location:** `/home/ubuntu/imk/streaming/flutter_app/lib/models/fighter.dart`

**Flutter Fighter:**
```dart
class Fighter {
  final String id;
  final String name;
  final String character;
  final String llmModel;
  final String imageAsset; // LOCAL asset path
  final double winRate;
  final int matchesPlayed;
  final int matchesWon;
}
```

### 1.4 Hardcoded Data (Flutter)

**Location:** `/home/ubuntu/imk/streaming/flutter_app/lib/services/mock_data_service.dart`

**Hardcoded Fighters:**
```dart
static const fighters = [
  Fighter(
    id: 'sub-zero',
    name: 'SUB-ZERO',
    character: 'Sub-Zero',
    llmModel: 'Claude Opus 4.6',  // ❌ Hardcoded
    imageAsset: Assets.fighterLeft, // ❌ Hardcoded local asset
    winRate: 0.62,  // ❌ Hardcoded
    matchesPlayed: 4151,  // ❌ Hardcoded
    matchesWon: 2574,
  ),
  // ... 4 more hardcoded fighters
];
```

**Hardcoded Matches:**
- 3 mock matches with hardcoded IDs, fighters, pools, odds
- Completely disconnected from backend

**Hardcoded Bets:**
- 4 mock user bets
- Won't reflect actual user betting

---

## 2. Current Bugs & Issues

### 2.1 Critical Issues

#### Bug #1: Fighter ↔ Agent Disconnect
**Problem:**
- Backend Fighter model has `agent_checkpoint` and `agent_architecture` fields
- These fields are **never used** when creating matches
- Matches use hardcoded agent strings: `p1_agent="random"`, `p2_agent="cpu"`
- No way to associate a Fighter DB record with a specific trained agent

**Impact:**
- Can't create matches with specific trained agents via fighters
- Fighter stats don't reflect agent performance
- Can't have "character-specific" trained agents

**Example:**
```python
# Current: Admin creates match
match = Match(
    p1_agent="random",  # ❌ Hardcoded string
    p2_agent="lstm",    # ❌ Not linked to Fighter
)

# Fighter record exists but isn't used
fighter = Fighter(
    name="Sub-Zero",
    agent_checkpoint="checkpoints/lstm.onnx",  # ❌ Ignored!
    agent_architecture="lstm",
)
```

#### Bug #2: Image Asset Mismatch
**Problem:**
- Flutter expects local assets: `Assets.fighterLeft`
- Backend serves `image_url` (remote URL)
- `Fighter.fromJson()` sets `imageAsset = ''` for backend data
- Result: No fighter images in app!

**Code:**
```dart
factory Fighter.fromJson(Map<String, dynamic> json) {
  return Fighter(
    imageAsset: '', // ❌ Empty! Backend has image_url
  );
}
```

#### Bug #3: Mock Data Override
**Problem:**
- Flutter uses `MockDataService.fighters` by default
- API-fetched fighters are ignored unless mock data fails
- Users see fake data instead of real matches

**Code Path:**
```dart
FighterProvider.refresh()
  → api.fetchFighters()
  → Returns real fighters
  → BUT mock data still used in UI!
```

#### Bug #4: No Agent Upload System
**Problem:**
- New trained agents must be manually copied to `backend/app/agents/checkpoints/`
- No API endpoint to upload .onnx files
- No validation of checkpoint format/compatibility
- No versioning or rollback

### 2.2 Minor Issues

1. **Inconsistent Naming:**
   - Backend: `slug` (e.g., "sub-zero")
   - Flutter: `id` (e.g., "sub-zero")
   - Match: `p1_agent` (e.g., "lstm") - different namespace!

2. **Missing Validation:**
   - No check if agent exists before creating match
   - No validation of character_id (must be 0-19 for MK4)

3. **No Character Images:**
   - Backend `image_url` field exists but not populated
   - No asset management system

4. **Win Rate Staleness:**
   - Flutter caches fighters
   - Win rates don't update unless app restarts

---

## 3. Data Flow Analysis

### 3.1 Current Match Creation Flow

```
Admin Panel (Web)
  ↓
POST /admin/matches/new
  ├─ Inputs: p1_agent="random", p2_agent="lstm", ...
  ├─ _ensure_fighter(agent_id, display_name)
  │   └─ Creates/finds Fighter with slug=agent_id
  ├─ match.fighter1_id = fighter.id
  ├─ match.p1_agent = "random"  ❌ Stored separately!
  └─ match_runner.start_match(match_id)
      └─ agents.create_agent(match.p1_agent)  ✅ Uses agent string
```

**Problem:** Fighter DB record created but agent is loaded by string ID!

### 3.2 Desired Match Creation Flow

```
Admin Panel
  ↓
Select Fighter from dropdown (e.g., "Sub-Zero [LSTM]")
  ↓
POST /admin/matches/new { fighter1_id: UUID, fighter2_id: UUID }
  ↓
Backend loads fighters
  ├─ fighter1.agent_architecture → "lstm"
  ├─ fighter1.agent_checkpoint → "checkpoints/lstm.onnx"
  └─ match_runner uses fighter's agent config
```

---

## 4. Missing Features

### 4.1 Agent Management
- ❌ No API to list available agents
- ❌ No API to upload new agent checkpoints
- ❌ No agent metadata (author, training date, performance)
- ❌ No agent versioning
- ❌ No checkpoint validation

### 4.2 Fighter Management (Admin)
- ✅ Create fighter (exists but limited)
- ❌ Edit fighter
- ❌ Delete fighter
- ❌ Upload character image
- ❌ Associate fighter with agent
- ❌ View fighter match history
- ❌ Bulk import fighters

### 4.3 Fighter Management (API)
- ✅ GET /api/fighters (basic)
- ❌ GET /api/fighters/{id}
- ❌ POST /api/fighters (create)
- ❌ PUT /api/fighters/{id} (update)
- ❌ DELETE /api/fighters/{id}
- ❌ POST /api/fighters/{id}/image (upload)

### 4.4 Flutter App
- ❌ Fighter detail view (beyond basic stats)
- ❌ Agent information display
- ❌ Real-time win rate updates
- ❌ Fighter comparison
- ❌ Filter by character/agent type

---

## 5. Database Schema Issues

### Current Schema:
```sql
CREATE TABLE fighters (
    id UUID PRIMARY KEY,
    name VARCHAR(100) UNIQUE,
    slug VARCHAR(50) UNIQUE,
    character VARCHAR(50),
    character_id INTEGER,
    llm_model VARCHAR(100),
    image_url VARCHAR(500),
    agent_checkpoint VARCHAR(500),  -- ❌ Not used!
    agent_architecture VARCHAR(50),  -- ❌ Not used!
    matches_played INTEGER DEFAULT 0,
    matches_won INTEGER DEFAULT 0,
    created_at TIMESTAMP
);

CREATE TABLE matches (
    ...
    fighter1_id UUID REFERENCES fighters(id),
    p1_agent VARCHAR(50),  -- ❌ Redundant!
    ...
);
```

### Proposed Schema Changes:

**Option A: Fighter = Agent (1:1)**
- Remove `p1_agent` from matches
- Use `fighter1.agent_architecture` + `fighter1.agent_checkpoint`
- Pros: Simpler, cleaner
- Cons: Can't run same agent with different fighters

**Option B: Separate Agent Table (N:M)**
```sql
CREATE TABLE agents (
    id UUID PRIMARY KEY,
    name VARCHAR(100),
    architecture VARCHAR(50),
    checkpoint_path VARCHAR(500),
    version INTEGER,
    uploaded_at TIMESTAMP,
    uploaded_by UUID REFERENCES users(id)
);

CREATE TABLE fighters (
    ...
    agent_id UUID REFERENCES agents(id),  -- Link to agent
);
```
- Pros: Flexible, versioning, one agent for multiple fighters
- Cons: More complex

**Recommendation:** Option B for production scalability

---

## 6. File Structure

### Current Structure:
```
backend/app/
├── agents/
│   ├── __init__.py (registry)
│   ├── base.py
│   ├── random_agent.py
│   ├── cpu_agent.py
│   ├── onnx_agent.py
│   └── checkpoints/
│       ├── lstm.onnx
│       ├── transformer.onnx
│       └── ...
├── api/
│   ├── fighters.py (GET only)
│   └── ...
└── admin_views.py (basic fighter CRUD)

flutter_app/lib/
├── models/
│   └── fighter.dart
├── providers/
│   └── fighter_provider.dart
├── services/
│   ├── api_service.dart
│   └── mock_data_service.dart  ❌ Overrides real data
└── screens/
    ├── fighter_overview_screen.dart
    └── fighter_details_screen.dart
```

### Proposed Structure:
```
backend/app/
├── agents/
│   ├── registry.py (discover, validate, upload)
│   ├── models.py (AgentMetadata schema)
│   └── storage/
│       ├── lstm_v1.onnx
│       ├── lstm_v2.onnx  (versioning)
│       └── metadata.json
├── api/
│   ├── fighters.py (full CRUD)
│   ├── agents.py (NEW - upload, list, download)
│   └── characters.py (NEW - MK4 character data)
└── uploads/
    └── fighter_images/

flutter_app/lib/
├── models/
│   ├── fighter.dart (updated with image handling)
│   └── agent.dart (NEW)
├── services/
│   ├── api_service.dart (full fighter CRUD)
│   └── image_cache_service.dart (NEW)
└── screens/
    ├── admin/
    │   ├── agent_upload_screen.dart (NEW)
    │   └── fighter_editor_screen.dart (NEW)
    └── fighter/
        ├── fighter_list_screen.dart
        └── fighter_detail_screen.dart (enhanced)
```

---

## 7. Technical Debt

### 7.1 Immediate Issues
1. **Mock data contamination** - Remove or make it dev-only
2. **Image asset system** - Use cached network images
3. **Fighter-Agent linkage** - Fix match creation to use fighter's agent
4. **API incompleteness** - Add missing CRUD endpoints

### 7.2 Medium-term Debt
1. **No agent versioning** - Can't rollback bad agents
2. **No checkpoint validation** - Could upload broken .onnx files
3. **No character images** - All fighters look the same
4. **No agent performance tracking** - Can't compare agent versions

### 7.3 Long-term Concerns
1. **Scalability** - Single checkpoints/ folder won't scale to 100s of agents
2. **Multi-tenancy** - Users can't upload private agents
3. **Asset CDN** - Should use S3/CDN for images
4. **Agent marketplace** - Future: users trade/sell agents

---

## 8. Dependencies & Constraints

### 8.1 MK4 Character Constraints
- **19 total characters** (0-18)
- Character IDs:
  - 0: Kai
  - 1: Sub-Zero
  - 2: Reiko
  - 3: Reptile
  - 4: Quan Chi
  - 5: Raiden
  - 6: Scorpion
  - 7: Shinnok
  - 8: Jax
  - 9: Jarek
  - 10: Liu Kang
  - 11: Sonya
  - 12: Tanya
  - 13: Fujin
  - 14: Johnny Cage

### 8.2 Agent Architecture Constraints
- ONNX format required
- Must implement specific input/output shapes
- Observation space: defined in `agents/observation.py`
- Action space: 18-dimensional discrete

### 8.3 Performance Constraints
- Agent inference must be <16ms (60fps gameplay)
- Checkpoint files: typically 100KB - 2MB
- Image uploads: <5MB per fighter

---

## 9. Security Considerations

### 9.1 Agent Upload Risks
- **Malicious ONNX files** - Could exploit ONNX runtime
- **File size bombs** - Huge models could DoS
- **Name collisions** - Overwrite existing agents

**Mitigations:**
- Validate ONNX format before loading
- Max file size: 10MB
- UUID-based filenames
- Sandbox ONNX runtime

### 9.2 Image Upload Risks
- **SSRF attacks** - If allowing URLs
- **XSS via SVG** - If allowing SVG uploads
- **Storage abuse** - Unlimited uploads

**Mitigations:**
- Only allow JPEG/PNG
- Max 5MB per image
- Scan with image library
- Rate limit uploads

---

## 10. Next Steps (Summary)

### Phase 1: Analysis & Planning ✅ (Current)
1. ✅ Map current architecture
2. ✅ Identify hardcoded data
3. ✅ Document bugs
4. ⏳ Create implementation plan (next)

### Phase 2: Foundation Fixes (Week 1)
1. Fix Fighter ↔ Agent linkage
2. Remove/disable mock data
3. Add missing API endpoints (Fighter CRUD)
4. Update Flutter models

### Phase 3: Image System (Week 1-2)
1. Add image upload API
2. Implement Flutter image caching
3. Migrate to remote images
4. Add character portraits

### Phase 4: Agent Upload (Week 2-3)
1. Design agent metadata schema
2. Add agent upload API
3. Implement checkpoint validation
4. Add admin UI for agent management

### Phase 5: Flutter Integration (Week 3-4)
1. Remove hardcoded fighters
2. Add fighter detail screens
3. Add agent info display
4. Real-time win rate updates

### Phase 6: Polish & Testing (Week 4)
1. Admin UI improvements
2. End-to-end testing
3. Performance optimization
4. Documentation

---

## 11. Open Questions

1. **Should fighters be character-specific or agent-specific?**
   - Option A: "Sub-Zero [LSTM]" vs "Sub-Zero [Transformer]" (multiple fighters per character)
   - Option B: "Sub-Zero" with swappable agent (one fighter per character)

2. **Who can upload agents?**
   - Admins only?
   - Verified users?
   - Anyone (marketplace model)?

3. **Agent naming convention?**
   - User-provided names?
   - Auto-generated (character + architecture + version)?
   - UUID-based?

4. **How to handle agent updates?**
   - Versioning (keep old checkpoints)?
   - Replace (break old matches)?
   - Immutable (new fighter record per version)?

5. **Character image source?**
   - Manually upload?
   - Pre-populated from game assets?
   - User-generated?

6. **Should agents be shareable?**
   - Public agent library?
   - Private agents per user?
   - Team-based access?

---

## 12. Estimation

### Complexity: **Medium-High**

**Reasons:**
- Multiple interconnected systems (backend agents, DB, API, Flutter)
- Data migration needed
- Image handling adds complexity
- ONNX validation non-trivial

**Time Estimate:**
- **Phase 2:** 2-3 days (foundation fixes)
- **Phase 3:** 2-3 days (image system)
- **Phase 4:** 3-4 days (agent upload + validation)
- **Phase 5:** 3-4 days (Flutter integration)
- **Phase 6:** 2 days (polish & testing)

**Total: 12-16 days** (2-3 weeks with buffer)

**Team Size:** 1-2 developers

---

## Appendix A: Character ID Mapping

| ID | Character | Notes |
|----|-----------|-------|
| 0 | Kai | Original MK4 |
| 1 | Sub-Zero | Klassic character |
| 2 | Reiko | MK4 debut |
| 3 | Reptile | Palette swap moveset |
| 4 | Quan Chi | Sorcerer |
| 5 | Raiden | Thunder god |
| 6 | Scorpion | Iconic ninja |
| 7 | Shinnok | Final boss |
| 8 | Jax | Military |
| 9 | Jarek | Black Dragon |
| 10 | Liu Kang | Protagonist |
| 11 | Sonya | Special Forces |
| 12 | Tanya | Traitor |
| 13 | Fujin | Wind god |
| 14 | Johnny Cage | Hollywood |

**Note:** IDs 15-18 exist but are unplayable/unused characters.

---

## Appendix B: Agent Architectures

| Architecture | Description | Checkpoint Size | Inference Time |
|--------------|-------------|-----------------|----------------|
| random | Weighted random | N/A | <1ms |
| cpu | No input (CPU AI) | N/A | <1ms |
| lstm | LSTM with BPTT | ~400KB | ~5ms |
| obj_belief | Slot attention + belief | ~13KB | ~8ms |
| disc_rssm | Discrete latent world model | ~440KB | ~10ms |
| transformer | Causal transformer | ~310KB | ~12ms |

---

*End of Analysis*

**Next Step:** Review this analysis, answer open questions, then proceed to implementation plan.
