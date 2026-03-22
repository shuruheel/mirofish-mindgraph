# MindGraph Target Architecture

**Date:** 2026-03-21
**Depends on:** `docs/current-architecture.md`, `docs/memory-gap-analysis.md`, `docs/adr/001-product-framing.md`

---

## 1. Design Principles

1. **Preserve existing functionality** — The current MiroFish pipeline must continue to work unchanged when MindGraph is disabled.
2. **Feature-flagged adoption** — Every MindGraph integration point is controlled by a feature flag. Dual-write mode enables comparison without commitment.
3. **Async, batched writes** — MindGraph writes never block the simulation hot path.
4. **Normalized event schema** — An internal event type sits between raw OASIS actions and MindGraph API calls. Neither OASIS format nor MindGraph format leaks across the boundary.
5. **Graceful degradation** — If MindGraph is unavailable, the system falls back to Zep-only mode without error.

---

## 2. Option A: MindGraph as Augmentation Layer (Recommended)

### Overview

MindGraph sits alongside Zep. Zep continues to be the primary knowledge graph for the simulation pipeline. MindGraph receives **structured projections** of simulation events — not raw text dumps, but typed nodes and edges in the appropriate cognitive layer.

```
Seed Documents ──→ Zep Graph (primary, unchanged)
                └──→ MindGraph Reality Layer (structured seed entities)

Simulation Events ──→ Zep Graph (activity text, unchanged)
                  └──→ MindGraph Epistemic/Memory Layers (structured beliefs, sessions)

ReportAgent ──→ Zep Search (existing tools)
            └──→ MindGraph Retrieve/Traverse (new structured queries)
```

### Architecture Changes

**New modules:**
- `backend/app/services/mindgraph_client.py` — Async HTTP client wrapping MindGraph REST API. Uses `httpx.AsyncClient` (or sync `httpx.Client` with threading, matching existing patterns).
- `backend/app/services/mindgraph_adapter.py` — Translates normalized internal events to MindGraph API calls. Contains mapping logic for each event type → MindGraph endpoint.
- `backend/app/services/event_normalizer.py` — Defines the internal `SimulationEvent` schema. Converts raw OASIS actions and Zep graph data into normalized events.
- `backend/app/services/mindgraph_memory_updater.py` — Parallel to `ZepGraphMemoryUpdater`. Background thread, batched writes, same queue pattern.

**Modified modules:**
- `backend/app/services/simulation_runner.py` — After writing to Zep queue, also writes to MindGraph queue (if enabled).
- `backend/app/services/graph_builder.py` — After Zep graph construction, optionally projects seed entities into MindGraph Reality layer.
- `backend/app/services/report_agent.py` — New tools: `MindGraphRetrieve`, `MindGraphTraverse` alongside existing Zep tools.
- `backend/app/services/zep_tools.py` — Augmented with MindGraph query fallbacks.
- `backend/app/config.py` — New config: `MINDGRAPH_API_KEY`, `MINDGRAPH_BASE_URL`, `MINDGRAPH_ENABLED`, `MINDGRAPH_WRITE_MODE` (off/dual/primary).

**Unchanged modules:**
- All frontend code
- All API routes (same interface, richer data behind the scenes)
- `ontology_generator.py`, `oasis_profile_generator.py`, `simulation_config_generator.py`
- `simulation_ipc.py`, `simulation_manager.py`
- OASIS scripts

### Data Flow: Graph Construction (Seed → Reality)

```
1. Existing: text → Zep graph (unchanged)
2. New: After Zep build completes, read entities/edges from Zep
3. New: For each entity → POST /reality/entity (create)
4. New: For each relationship → POST /reality/entity (relate)
5. New: For source documents → POST /reality/capture (source + snippets)
```

### Data Flow: Simulation (Activities → Epistemic + Memory)

```
1. OASIS writes actions.jsonl (unchanged)
2. SimulationRunner reads actions, creates AgentAction (unchanged)
3. AgentAction → event_normalizer → SimulationEvent (new)
4. SimulationEvent → ZepGraphMemoryUpdater (unchanged path)
5. SimulationEvent → MindGraphMemoryUpdater (new path, feature-flagged)
   a. Content-bearing actions (CREATE_POST, CREATE_COMMENT, QUOTE_POST):
      → Filter for substantive content (not "DO_NOTHING", not empty)
      → POST /epistemic/argument (claim + optional evidence)
      → Confidence derived from agent's influence_weight + sentiment
   b. Social actions (LIKE, DISLIKE, FOLLOW, REPOST):
      → POST /memory/session trace entry (lightweight telemetry)
   c. Round boundaries:
      → POST /memory/session trace (round marker)
   d. Simulation start/end:
      → POST /memory/session open/close
```

### Data Flow: Report Generation (Query Augmentation)

```
Existing tools (unchanged):
- InsightForge → Zep search
- PanoramaSearch → Zep full graph read
- QuickSearch → Zep edge search
- InterviewAgents → IPC to OASIS

New tools (added):
- MindGraphRetrieve → POST /retrieve (text, semantic, hybrid)
  Pre-built: unresolved_contradictions, weak_claims, open_questions
- MindGraphTraverse → POST /traverse (chain, neighborhood, subgraph)
  Follow reasoning chains, find belief clusters
- MindGraphHistory → POST /evolve (history)
  Track belief evolution over simulation rounds
```

### Pros
- **Minimal disruption** — Existing pipeline works unchanged. MindGraph is additive.
- **Incremental adoption** — Can be enabled/disabled per simulation.
- **Comparison mode** — Run same simulation with and without MindGraph to measure improvement.
- **Clear failure isolation** — MindGraph failure doesn't affect simulation or Zep-based reporting.

### Cons
- **Dual-write overhead** — Two external API targets per simulation event.
- **Potential consistency issues** — Zep and MindGraph may disagree on entity resolution.
- **Increased operational complexity** — Two external services to monitor.

### Migration Difficulty: Low-Medium
- No changes to existing data models or storage
- New modules are additive
- Feature flags control adoption
- Rollback = disable feature flag

### Performance Impact
- **Latency:** <50ms additional per batch (async, non-blocking)
- **Storage:** ~2x for simulation events (Zep + MindGraph)
- **API calls:** +1 per batch of 5 simulation events

---

## 3. Option B: MindGraph as Primary Cognitive Memory

### Overview

MindGraph replaces Zep as the memory backend. A compatibility shim translates existing Zep-shaped queries into MindGraph retrieve/traverse calls. All simulation events flow through MindGraph.

```
Seed Documents ──→ MindGraph Reality Layer (primary)
                └──→ Zep Graph (compatibility, read-only after migration)

Simulation Events ──→ MindGraph Epistemic/Memory Layers (primary)
                  ╳ Zep Graph (removed)

ReportAgent ──→ MindGraph Retrieve/Traverse (primary)
            ╳ Zep Search (removed, replaced by shim)
```

### Architecture Changes

**Replaced modules:**
- `graph_builder.py` → `mindgraph_graph_builder.py` — Build graph directly in MindGraph
- `zep_graph_memory_updater.py` → `mindgraph_memory_updater.py` — Only MindGraph writes
- `zep_tools.py` → `mindgraph_tools.py` — All queries against MindGraph
- `zep_entity_reader.py` → `mindgraph_entity_reader.py` — Read entities from MindGraph

**Compatibility shim:**
- `zep_compat.py` — Translates `client.graph.search()` calls to `POST /retrieve`, `fetch_all_nodes()` to `POST /traverse (subgraph)`, etc.

### Pros
- **Single source of truth** — No dual-write, no consistency issues
- **Full MindGraph capabilities** — No compromises on what can be queried
- **Simpler operational model** — One external service instead of two

### Cons
- **High migration risk** — Must replace every Zep callsite (13+ locations, see callsite map)
- **Compatibility shim complexity** — Zep and MindGraph have different data models; translation is lossy
- **No fallback** — If MindGraph is down, the system is down
- **OASIS profile generation** — Currently uses Zep search for context enrichment; must be rewritten
- **Graph construction** — Current dynamic Pydantic class generation for Zep ontology has no MindGraph equivalent; need a different approach
- **Frontend impact** — GraphPanel.vue expects Zep node/edge format; needs adapter

### Migration Difficulty: High
- Every Zep callsite must be replaced or shimmed
- Data migration from existing Zep graphs
- Extensive testing required
- Rollback requires keeping Zep infrastructure warm

### Performance Impact
- **Latency:** Depends on MindGraph API performance (unknown until tested)
- **Storage:** Potentially lower (single backend)
- **Risk:** Unknown performance characteristics under load

---

## 4. Recommendation

**Option A: MindGraph as Augmentation Layer.**

Rationale:
1. Aligned with ADR-001 (prediction sandbox first, cognitive engine later)
2. Preserves all existing functionality without regression risk
3. Enables A/B comparison of report quality
4. If MindGraph proves superior in dual-write mode, migration to Option B becomes a data-informed decision rather than a speculative one
5. The adapter layer designed for Option A is the same adapter layer needed for Option B — no wasted work

The adapter interface should be:
```python
class CognitiveMemoryProvider(Protocol):
    def capture_reality(self, source: Source, entities: list[Entity]) -> None: ...
    def record_claim(self, agent_id: str, claim: Claim, evidence: list[Evidence]) -> None: ...
    def open_session(self, simulation_id: str) -> str: ...
    def close_session(self, session_id: str) -> None: ...
    def add_trace(self, session_id: str, event: SimulationEvent) -> None: ...
    def retrieve(self, query: str, mode: str, filters: dict) -> list[Result]: ...
    def traverse(self, start: str, pattern: str, depth: int) -> Graph: ...
```

This interface can be implemented by `MindGraphProvider` now and by `ZepProvider` (shimmed) if needed later.

---

## 5. Target Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (Vue 3)                      │
│  Home → Process → Simulation → Report → Interaction          │
└──────────────────────┬──────────────────────────────────────┘
                       │ Axios /api/*
┌──────────────────────▼──────────────────────────────────────┐
│                     Flask Backend                            │
│                                                              │
│  ┌─────────┐  ┌──────────────┐  ┌────────────┐              │
│  │ Graph   │  │ Simulation   │  │  Report    │              │
│  │ API     │  │ API          │  │  API       │              │
│  └────┬────┘  └──────┬───────┘  └─────┬──────┘              │
│       │              │                │                      │
│  ┌────▼────────────┐ │  ┌─────────────▼──────────┐          │
│  │ GraphBuilder    │ │  │ ReportAgent            │          │
│  │ OntologyGen     │ │  │ ZepTools (existing)    │          │
│  └────┬───────┬────┘ │  │ MindGraphTools (NEW)   │          │
│       │       │      │  └────────────────────────┘          │
│       │       │      │                                       │
│  ┌────▼───┐ ┌─▼─────────────┐ ┌───────────────────────┐    │
│  │  Zep   │ │ MindGraph     │ │ SimulationRunner      │    │
│  │ Client │ │ Adapter (NEW) │ │   ├─ ZepMemoryUpdater │    │
│  └────────┘ └───────────────┘ │   └─ MGMemoryUpd(NEW) │    │
│                               └───────────┬───────────┘    │
│                                           │ subprocess     │
│                                    ┌──────▼──────┐         │
│                                    │ OASIS       │         │
│                                    │ Scripts     │         │
│                                    └─────────────┘         │
└─────────────────────────────────────────────────────────────┘
                    │                │
            ┌───────▼───┐    ┌──────▼───────┐
            │ Zep Cloud │    │  MindGraph   │
            │ (primary) │    │  Cloud (NEW) │
            └───────────┘    └──────────────┘
```

---

## 6. Key Decisions Deferred

These require their own ADRs (Phases 3-5):

- **ADR-002:** Memory architecture choice (this document recommends A; ADR formalizes it)
- **ADR-003:** Zep coexistence strategy
- **ADR-004:** Epistemic write granularity (what triggers a Claim vs. a Trace entry)
- **ADR-005:** Event normalization schema
- **ADR-006:** Graph isolation (per-simulation vs. shared persistent graph)
