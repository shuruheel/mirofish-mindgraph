# MiroFish × MindGraph Integration Analysis

> Last updated: 2026-03-22
> Status: **Fully migrated to mindgraph-sdk v0.1.3 — 36/36 SDK integration tests pass**

## Overview

MindGraph Cloud fully replaces Zep Cloud as MiroFish's knowledge graph backend. The integration uses the official `mindgraph-sdk` Python package (v0.1.3) wrapped in a thin client layer that adds retry logic and project-level namespace isolation.

---

## Current State

### What's Done

| Milestone | Status |
|-----------|--------|
| Custom REST client → mindgraph-sdk wrapper | Complete |
| All 6 services import and work with SDK client | Complete |
| 36/36 SDK integration tests pass against live API | Complete |
| Code review findings (10 issues) fixed | Complete |
| All Zep references removed from backend Python code | Complete |
| Flask app starts cleanly with all 3 blueprints | Complete |
| OASIS/CAMEL-AI dependencies installed (Python 3.12) | Complete |

### What's Remaining

| Item | Severity | Notes |
|------|----------|-------|
| Frontend has 3 stale "Zep" references in Vue components | Low | Cosmetic — UI text only, no functional impact |
| `docs/` directory has 137 Zep references across 12 files | Low | Historical docs from pre-migration planning |
| No end-to-end test with real LLM key | Medium | Graph build + simulation + report pipeline untested as a whole |
| `retrieve_context()` uses SDK private `_request()` method | Low | SDK lacks `agent_id` on `retrieve_context()` — tracked, SDK pinned `<0.2` |
| Cognitive queries (`weak_claims`, etc.) are global, not project-scoped | Low | Dedicated GET endpoints don't support `agent_id` filtering |
| `decay_salience()` is global, not project-scoped | Low | MindGraph API limitation — warning log added |
| `list_nodes()` fallback path returns unfiltered global nodes | Low | Primary path (`get_agent_nodes`) works; fallback only triggers on error |

---

## Architecture

### ID Hierarchy

MiroFish has three distinct ID concepts that map to MindGraph:

| MiroFish Concept | Format | Maps To |
|---|---|---|
| **Project** (`project_id`) | `proj_xxxx` | Local container for files, ontology, text |
| **Graph** (`graph_id`) | `mirofish_xxxx` | MindGraph `agent_id` namespace — all API calls use this |
| **Simulation Agent** | Human name (e.g., "张明") | Agent-type node *within* the graph namespace |

Key distinction: MindGraph's `agent_id` is used as a **project namespace**, not to identify individual simulation agents. Simulation agents are data nodes inside that namespace.

### Namespace Isolation

MindGraph has one org-level graph per API key. MiroFish achieves project isolation via:
- **Writes:** All requests include `agent_id=graph_id`
- **Reads:** `get_agent_nodes(agent_id)` for namespace-filtered node listing
- **Edges:** `get_edges(from_uid=...)` per node (no global agent filter on edges)

### Key Files

| File | Role |
|------|------|
| `app/utils/mindgraph_client.py` | SDK wrapper (~500 lines) — retry logic + `project_id` → `agent_id` mapping |
| `app/services/graph_builder.py` | Seed document ingestion and graph construction |
| `app/services/entity_reader.py` | Entity extraction from graph for simulation setup |
| `app/services/simulation_manager.py` | Registers Agent/Goal/Hypothesis nodes |
| `app/services/graph_memory_updater.py` | Real-time simulation activity → graph ingestion |
| `app/services/graph_tools.py` | Search/retrieval tools for report agent |
| `app/services/report_agent.py` | 7-tool ReACT agent for report generation |
| `app/services/simulation_runner.py` | Loads agent_node_uids, passes to memory updater |

### Data Flow

```
Document Upload → TextProcessor → ingest_chunk/ingest_document → MindGraph
                                                                    ↓
Simulation Prep → Agent profiles → register_agent_node (Agent nodes)
                                 → create_goal (non-neutral agents)
                                 → add_hypothesis (prediction question)
                                                                    ↓
Simulation Run  → Agent actions → ingest_agent_post (auto-extraction)
                                → add_link (AUTHORED edges)
                                → record_decision (3-step deliberation)
                                → record_anomaly (stance inconsistency)
                                → trace_session (social actions)
                                → decay_salience (round-end, global)
                                → capture_observation (round-end events)
                                                                    ↓
Simulation End  → distill (summary node from epistemic UIDs)
               → record_pattern (emergent behavior detection)
               → close_session
                                                                    ↓
Report Gen      → search_hybrid / retrieve_context / cognitive queries
               → graph_explore (reasoning chains, belief history)
               → interview_agents (IPC to OASIS)
```

---

## SDK Wrapper Design

### `MindGraphClient` (app/utils/mindgraph_client.py)

Wraps `mindgraph.MindGraph` SDK class. Design principles:

1. **Consistent `project_id` interface** — Every public method accepts `project_id`, internally maps to SDK's `agent_id`. Callers never see MindGraph's namespace concept.

2. **Retry with exponential backoff** — 3 retries, skips 4xx errors (except 429). Only retries network-level exceptions (`httpx.HTTPError`, `ConnectionError`, `TimeoutError`, `OSError`). Programming errors (`TypeError`, `AttributeError`) fail immediately.

3. **Response normalization** — SDK sometimes returns `list` where callers expect `dict`. The wrapper normalizes: `list` → `{"results": list}`, `list` → `{"nodes": list, "edges": list}`, etc.

4. **Resource management** — `close()` method and context manager (`with MindGraphClient() as client:`) for proper httpx connection pool cleanup.

### SDK Methods Used

| MindGraphClient Method | SDK Method | Endpoint |
|---|---|---|
| `ingest_chunk` | `ingest_chunk` | `POST /ingest/chunk` |
| `ingest_document` | `ingest_document` | `POST /ingest/document` |
| `search_hybrid` | `retrieve(action="hybrid")` | `POST /retrieve` |
| `retrieve_context` | `_request("POST", "/retrieve/context")` | `POST /retrieve/context` (private — SDK gap) |
| `get_weak_claims` | `get_weak_claims()` | `GET /claims/weak` |
| `get_contradictions` | `get_contradictions()` | `GET /contradictions` |
| `get_open_questions` | `get_open_questions()` | `GET /questions` |
| `list_all_nodes` | `get_agent_nodes(agent_id)` | `GET /nodes/agent/{id}` |
| `list_all_edges` | `get_edges(from_uid=...)` per node | `GET /edges?from_uid=...` |
| `get_node` | `get_node(uid)` | `GET /node/{uid}` |
| `get_neighborhood` | `neighborhood(uid)` + `get_edges(from_uid/to_uid)` | `POST /traverse` + `GET /edges` |
| `create_entity` | `find_or_create_entity` | `POST /reality/entity` |
| `resolve_entity` | `resolve_entity` | `POST /reality/entity` (action=resolve) |
| `register_agent_node` | `add_node(node_type="Agent")` | `POST /node` (auto `props._type`) |
| `add_link` | `add_link` | `POST /link` |
| `record_decision` | `open_decision` → `add_option` → `resolve_decision` | `POST /intent/deliberation` (3 calls) |
| `add_hypothesis` | `inquire(action="hypothesis")` | `POST /epistemic/inquiry` |
| `record_anomaly` | `inquire(action="anomaly")` | `POST /epistemic/inquiry` |
| `open_session` / `close_session` | `session(action="open/close")` | `POST /memory/session` |
| `distill` | `distill` | `POST /memory/distill` |
| `decay_salience` | `decay()` | `POST /evolve` (global) |
| `capture_observation` | `capture(action="observation")` | `POST /reality/capture` |

---

## Cognitive Layer Utilization

### Reality Layer — Seed Knowledge
- **Entity anchors** pre-created during ontology setup to guide entity resolution
- **Observations** recorded at round-end events
- **Entity CRUD** (create, relate, resolve, fuzzy_resolve) for graph construction

### Epistemic Layer — Agent Discourse
- **Auto-extraction** via `ingest_chunk` with `layers=["reality", "epistemic"]`
- **Structured hypothesis** for the simulation's prediction question
- **Anomaly nodes** when agents act against their configured stance
- **Pattern nodes** for emergent behaviors detected post-simulation
- **Cognitive queries** via dedicated GET endpoints for the report agent

### Intent Layer — Agent Motivation
- **Goal nodes** for non-neutral agents
- **Decision nodes** with 3-step lifecycle (open → option → resolve)
- **Agent→Goal** and **Agent→Decision** edges

### Memory Layer — Simulation Lifecycle
- **Session** wraps entire simulation run
- **Traces** for social actions
- **Distillation** at simulation end

### Agent Layer — Participant Identity
- **Agent nodes** with stance, sentiment, influence metadata
- **AUTHORED/DECIDED/EXHIBITED/HAS_GOAL** edges for attribution

### Action Layer — Not Used
OASIS manages action execution opaquely.

---

## API Issues & Fixes

### Phase 1: Initial API Integration (custom REST client)

| # | Issue | Fix |
|---|-------|-----|
| 1 | `POST /retrieve` returns list, not `{results:[...]}` | Response normalization in wrapper |
| 2 | `semantic` search returns 501 | Fallback to `hybrid` |
| 3 | `GET /edges` requires `from_uid` or `to_uid` | Iterate per-node edges with dedup |
| 4 | `neighborhood`/`chain` return flat lists | Wrap into structured dicts |
| 5 | `POST /node` requires `props._type` | SDK v0.1.3 auto-injects this |
| 6 | Decision resolve needs `chosen_option_uid` | 3-step flow captures UID at each step |

### Phase 2: SDK Migration + Code Review Fixes

| # | Issue | Fix |
|---|-------|-----|
| 7 | `delete_project_data()` used `list_nodes()` (no namespace filter) | Switched to `list_all_nodes()` (uses `get_agent_nodes`) |
| 8 | `retrieve_context()` calls private `_request()` | Documented; SDK pinned `>=0.1.3,<0.2` |
| 9 | Cognitive queries routed through `retrieve()` | Switched to dedicated SDK methods |
| 10 | `delete_project_data()` could loop infinitely | Added `max_iterations=100` safety limit |
| 11 | `get_neighborhood()` always returned empty edges | Now fetches edges via `get_edges(from_uid/to_uid)` |
| 12 | `_with_retry` retried programming errors | Narrowed to network exceptions only |
| 13 | SDK client never closed | Added `close()` + context manager |
| 14 | `decay_project_salience()` misleading name | Renamed to `decay_salience()`, added global-scope warning |
| 15 | `add_link`/`add_edge` exposed raw `agent_id` param | Added `project_id` param for consistency |
| 16 | 20 stale "Zep" references in backend Python | All replaced with "MindGraph" |

---

## Test Results

### SDK-Based Integration Tests: 36/36 Pass

Tested against live `api.mindgraph.cloud` on 2026-03-22:

| Category | Tests | Status |
|----------|-------|--------|
| Ingestion (chunk, agent_post, document) | 3 | Pass |
| Search (hybrid, text, RAG context) | 3 | Pass |
| Cognitive queries (weak_claims, contradictions, open_questions) | 3 | Pass |
| Node/edge listing (get_agent_nodes, per-node edges) | 2 | Pass |
| Single node (get, neighborhood, history, chain) | 4 | Pass |
| Entity CRUD (create, resolve, fuzzy_resolve) | 3 | Pass |
| Agent registration (add_node with auto props._type) | 1 | Pass |
| Epistemic layer (hypothesis, anomaly, pattern, claim) | 4 | Pass |
| Intent layer (goal, decision 3-step) | 2 | Pass |
| Memory layer (session open/trace/close, distill) | 4 | Pass |
| Edge creation (AUTHORED, HAS_GOAL) | 2 | Pass |
| Lifecycle (decay, observation) | 2 | Pass |
| Job polling (get_job) | 1 | Pass |
| Graph statistics | 1 | Pass |
| Client lifecycle (close) | 1 | Pass |

### Raw API Test: 26/43 Pass

The standalone test (`tests/test_mindgraph_integration.py`) uses raw `requests` without the SDK. Its 17 failures are response format issues the SDK handles (list vs dict normalization, missing params). This test is preserved as an API behavior reference but does not reflect production integration quality.

---

## Remaining Blockers for End-to-End Run

### Must-Fix Before Running

**None.** All backend code compiles, imports, and connects to MindGraph Cloud. The only requirements are:

1. **`MINDGRAPH_API_KEY`** — Set in `.env` file
2. **`LLM_API_KEY`** + `LLM_BASE_URL` + `LLM_MODEL_NAME` — Required for ontology generation, profile generation, simulation config, and report generation
3. **OASIS/CAMEL-AI** — Already installed (`camel-ai==0.2.78`, `camel-oasis==0.2.5`)

### Nice-to-Fix

| Item | Impact | Effort |
|------|--------|--------|
| 3 Zep references in frontend Vue components (cosmetic UI text) | User-facing text says "Zep" | 5 min |
| Historical docs reference Zep extensively | Confusion for new contributors | 30 min |
| `retrieve_context()` private API access | Fragile on SDK upgrade | SDK fix needed |
| Cognitive queries return global results | May include cross-project data | MindGraph API enhancement needed |
| `decay_salience()` is global | Affects all projects' salience scores | MindGraph API enhancement needed |
| Raw API test file uses old method names | Test won't run as-is | 15 min |

### SDK Enhancement Wishlist (for mindgraph-sdk v0.2+)

1. Add `agent_id` parameter to `retrieve_context()`
2. Add `agent_id` parameter to `get_weak_claims()`, `get_contradictions()`, `get_open_questions()`
3. Add `agent_id` parameter to `decay()`
4. Add `agent_id` filtering on `GET /edges`
