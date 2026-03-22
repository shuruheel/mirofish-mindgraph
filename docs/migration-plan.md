# Migration Plan: MindGraph Integration

**Date:** 2026-03-21
**Depends on:** All ADRs (001-006), `docs/mindgraph-target-architecture.md`

---

## Phase Overview

| Phase | Name | Duration Est. | Risk |
|-------|------|---------------|------|
| 1 | Foundation: Config, Client, Event Schema | 3-4 days | Low |
| 2 | Reality Layer: Seed Projection | 2-3 days | Low |
| 3 | Memory Layer: Session & Trace | 2-3 days | Low |
| 4 | Epistemic Layer: Claims & Beliefs | 3-4 days | Medium |
| 5 | Retrieval: ReportAgent Tools | 3-4 days | Medium |
| 6 | Dual-Write Mode & Testing | 2-3 days | Low |
| 7 | Optional: Deeper Integration | TBD | High |

---

## Phase 1: Foundation

**Goal:** Configuration, MindGraph client, event normalization schema, and feature flags.

### New Files

**`backend/app/config.py`** — Add:
```python
# MindGraph配置
MINDGRAPH_API_KEY = os.environ.get('MINDGRAPH_API_KEY')
MINDGRAPH_BASE_URL = os.environ.get('MINDGRAPH_BASE_URL', 'https://api.mindgraph.cloud')
MINDGRAPH_ENABLED = os.environ.get('MINDGRAPH_ENABLED', 'false').lower() == 'true'
MINDGRAPH_WRITE_MODE = os.environ.get('MINDGRAPH_WRITE_MODE', 'off')  # off | dual | mindgraph_only
```

**`backend/app/services/mindgraph_client.py`** — HTTP client wrapping MindGraph REST API. Sync requests using `requests` library (matching existing patterns — no async). Key methods:
- `reality_capture(source, snippets)`
- `reality_entity_create(entity_data)`
- `reality_entity_relate(source, target, relation)`
- `epistemic_argument(claim, evidence, warrant)`
- `memory_session_open(metadata)` / `memory_session_close(session_id)`
- `memory_session_trace(session_id, entries)`
- `retrieve(query, mode, filters)`
- `traverse(start, pattern, depth)`
- `evolve_history(node_id)`

**`backend/app/services/event_normalizer.py`** — Defines `SimulationEvent` hierarchy (per ADR-005). Converts `AgentAction` → typed event:
- Content-bearing actions → `ClaimEvent`
- Social actions → `SocialActionEvent`
- Round boundaries → `RoundEvent`
- Session lifecycle → `SessionEvent`

**`.env.example`** — Add MindGraph config entries.

### Impacted Files
- `backend/app/config.py` — New config variables
- `.env.example` — New entries

### Backward Compatibility
- All new. No existing behavior changed.
- `MINDGRAPH_ENABLED=false` (default) means zero impact.

### Testing Strategy
- Unit tests for event_normalizer: verify each AgentAction type maps to correct SimulationEvent
- Unit tests for mindgraph_client: mock HTTP responses

### Rollback
- Delete new files. Remove config entries.

---

## Phase 2: Reality Layer — Seed Projection

**Goal:** After Zep graph construction completes, project seed entities and relationships into MindGraph's Reality layer.

### New Files

**`backend/app/services/mindgraph_reality_projector.py`** — Reads completed Zep graph (nodes + edges) and writes to MindGraph:
1. Create `Source` node for each uploaded document
2. Create `Entity` nodes for each Zep entity (carrying `zep_entity_uuid` in metadata)
3. Create relationships between entities via `/reality/entity` relate
4. Create `Observation` nodes for key facts (edge facts from Zep)

### Impacted Files
- `backend/app/api/graph.py` — In the `build_task()` closure, after graph completion, call `MindGraphRealityProjector.project()` if enabled. Wrapped in try/except — failure does not fail the graph build.
- `backend/app/services/graph_builder.py` — No changes (Zep path unchanged).

### Data Model Changes
- `SimulationState` and `Project` — Add optional `mindgraph_graph_id` field.

### Backward Compatibility
- Feature-flagged. Zep graph build is unmodified.
- MindGraph projection runs after Zep build succeeds, in the same background thread.

### Testing Strategy
- Integration test: build a Zep graph from test document, verify MindGraph Reality nodes are created
- Verify `zep_entity_uuid` cross-references are correct

### Rollback
- Set `MINDGRAPH_ENABLED=false`. MindGraph projection is skipped. No Zep impact.

---

## Phase 3: Memory Layer — Sessions & Traces

**Goal:** Wrap each simulation run in a MindGraph session. Write per-round trace entries.

### New Files

**`backend/app/services/mindgraph_memory_updater.py`** — Parallel to `ZepGraphMemoryUpdater`:
- On simulation start: `POST /memory/session` (action: open)
- On each round boundary: `POST /memory/session` (add trace entry)
- On social actions: batch trace entries (same queue + batch pattern as Zep updater)
- On simulation end: `POST /memory/session` (action: close)
- Background thread, batch size 5, retry with backoff

### Impacted Files
- `backend/app/services/simulation_runner.py`:
  - In `start_simulation()`: Create MindGraph memory updater alongside Zep updater (if enabled)
  - In `_monitor_simulation()`: Feed normalized events to MindGraph updater
  - In `finally` block: Stop MindGraph updater alongside Zep updater
- `backend/app/services/simulation_manager.py`:
  - `SimulationState` — Add `mindgraph_session_id` field

### Backward Compatibility
- Feature-flagged. Zep memory updater is unmodified.
- MindGraph updater runs in parallel. Its failure doesn't affect simulation.

### Testing Strategy
- Integration test: run a short simulation (3 rounds, 5 agents), verify MindGraph session + traces created
- Verify round boundaries are marked correctly
- Stress test: verify batch buffering works under load

### Rollback
- Set `MINDGRAPH_WRITE_MODE=off`. MindGraph updater is not created.

---

## Phase 4: Epistemic Layer — Claims & Beliefs

**Goal:** Content-bearing simulation actions become structured epistemic claims in MindGraph.

### New Files

**`backend/app/services/mindgraph_epistemic_writer.py`** — Processes `ClaimEvent` objects:
- `POST /epistemic/argument` with:
  - `claim`: Agent's statement text
  - `confidence`: Derived from agent's influence_weight (ADR-004)
  - `agent_id`: MindGraph agent identifier
  - `evidence`: Referenced content (if QUOTE_POST or CREATE_COMMENT)
- For contradicting claims: detect via content similarity (future enhancement)
- Batched writes, same async pattern as memory updater

### Impacted Files
- `backend/app/services/mindgraph_memory_updater.py` — Route `ClaimEvent` to epistemic writer instead of trace
- `backend/app/services/event_normalizer.py` — Confidence calculation logic

### Data Model Changes
- None to existing models. New MindGraph nodes only.

### Backward Compatibility
- Feature-flagged. Only runs when `MINDGRAPH_WRITE_MODE=dual`.
- Zep receives the same text episodes as before.

### Testing Strategy
- Unit test: verify ClaimEvent → MindGraph argument API call mapping
- Integration test: run simulation, query MindGraph for claims, verify content and confidence
- Verify write granularity: only content-bearing actions produce claims

### Rollback
- Set `MINDGRAPH_WRITE_MODE=off`. No epistemic writes.

---

## Phase 5: Retrieval — ReportAgent Tools

**Goal:** Add MindGraph-powered tools to the ReportAgent's toolkit.

### New Files

**`backend/app/services/mindgraph_tools.py`** — Three new tools:

1. **MindGraphRetrieve** — `POST /retrieve`
   - Pre-built queries: `unresolved_contradictions`, `weak_claims`, `open_questions`
   - Hybrid search: text + semantic
   - Returns structured results with confidence scores

2. **MindGraphTraverse** — `POST /traverse`
   - `chain`: Follow reasoning chains from a claim
   - `neighborhood`: Get all connected nodes around an entity
   - `subgraph`: Extract a subgraph around a topic

3. **MindGraphHistory** — `POST /evolve` (action: history)
   - Track how a specific claim or entity evolved over simulation rounds
   - Belief revision timeline

### Impacted Files
- `backend/app/services/report_agent.py`:
  - Add MindGraph tools to the tool descriptions in `SECTION_SYSTEM_PROMPT_TEMPLATE`
  - Add tool dispatch cases in the ReACT loop
  - Conditionally include MindGraph tools based on feature flag
- `backend/app/services/zep_tools.py` — No changes (existing tools preserved).

### Backward Compatibility
- When MindGraph is disabled, ReportAgent uses only Zep tools (current behavior).
- When enabled, additional tools appear in the prompt. LLM can choose which to use.

### Testing Strategy
- Unit test: verify each MindGraph tool produces correct API calls
- Integration test: generate a report with MindGraph tools enabled, compare quality vs. Zep-only
- A/B test: same simulation, report with and without MindGraph tools

### Rollback
- Set `MINDGRAPH_ENABLED=false`. MindGraph tools are excluded from prompt.

---

## Phase 6: Dual-Write Mode & Testing

**Goal:** End-to-end testing of the full dual-write pipeline. Comparison benchmarks.

### Activities
1. Run existing demo scenario (e.g., Wuhan University simulation) through both paths
2. Compare report quality (subjective evaluation)
3. Measure latency impact per simulation round
4. Measure total graph size in MindGraph vs. Zep
5. Test graceful degradation (disable MindGraph mid-simulation)
6. Test feature flag transitions (off → dual → off)
7. Document findings in `docs/evaluation-results.md`

### Testing Strategy
- Full pipeline test: upload → ontology → graph → prepare → simulate → report → interact
- Regression: existing API endpoints return same structure
- Performance: timing comparison (rounds/minute with and without MindGraph)

---

## Phase 7: Optional Deeper Integration

**Goal:** If Phase 6 validates the approach, consider:
- **MindGraph entity resolution** for ontology stabilization
- **Intent layer** for agent goal tracking (requires OASIS integration changes)
- **Memory decay** via `/evolve` (action: decay) between simulation rounds
- **Cross-run persistence** (ADR-006 revisited)
- **Promote MindGraph to primary** (ADR-002 Option B)

This phase is not planned until Phase 6 results are evaluated.

---

## Risk Summary

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| MindGraph API latency exceeds budget | Medium | Medium | Async writes, batch buffering, hard timeout |
| MindGraph API unavailability | Low | Low | Feature flag, graceful degradation, Zep fallback |
| Entity resolution mismatch (Zep vs. MindGraph) | Medium | Low | Zep authoritative (ADR-003), cross-reference via metadata |
| Epistemic write volume overwhelms MindGraph | Low | Medium | Content-based filtering (ADR-004), batch size tuning |
| Report quality regression | Low | High | A/B testing in Phase 6, feature flag rollback |
| Complexity of maintaining two memory backends | Medium | Medium | Clean adapter interface, potential future consolidation |
