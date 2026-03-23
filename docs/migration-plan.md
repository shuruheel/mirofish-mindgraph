# Migration Plan: MindGraph Integration

**Date:** 2026-03-21 (original), 2026-03-22 (updated)
**Status:** COMPLETED — Zep fully replaced by MindGraph via `mindgraph-sdk` v0.1.3

---

## Outcome Summary

The original plan proposed a 7-phase dual-write migration (Zep + MindGraph in parallel). The actual implementation took a different, faster path: **direct replacement** of Zep with MindGraph in a single pass, skipping dual-write entirely.

| Original Phase | Planned | Actual |
|---|---|---|
| Phase 1: Foundation | Config, client, event schema | Done — `mindgraph_client.py` wraps SDK, config in `config.py` |
| Phase 2: Reality Layer | Seed projection alongside Zep | Done — MindGraph is sole backend, no Zep |
| Phase 3: Memory Layer | Sessions & traces in parallel | Done — `graph_memory_updater.py` uses MindGraph sessions |
| Phase 4: Epistemic Layer | Claims & beliefs in parallel | Done — Auto-extraction via `ingest_chunk`, cognitive queries via SDK |
| Phase 5: Retrieval Tools | MindGraph tools alongside Zep | Done — `graph_tools.py` uses MindGraph exclusively |
| Phase 6: Dual-Write Testing | A/B comparison | Skipped — direct replacement, no dual-write |
| Phase 7: Deeper Integration | Intent layer, decay, cross-run | Done — Goal/Decision/Anomaly nodes, decay, distillation |

---

## What Changed vs. the Plan

### Architecture Decision
- **Planned:** Dual-write mode with feature flags (`MINDGRAPH_ENABLED`, `MINDGRAPH_WRITE_MODE`)
- **Actual:** Full replacement. No feature flags, no Zep fallback. MindGraph is the only graph backend.

### Client Layer
- **Planned:** Custom REST client using `requests`
- **Actual:** Initially custom REST client (~1050 lines), then migrated to `mindgraph-sdk` wrapper (~500 lines) with retry logic

### Event Normalization
- **Planned:** `event_normalizer.py` converting `AgentAction` → typed events
- **Actual:** Not needed as a separate module. `graph_memory_updater.py` handles action-to-MindGraph-call routing directly (content actions → `ingest_chunk`, social actions → `trace_session`, decisions → `record_decision`).

### Why Dual-Write Was Skipped
1. Zep Cloud was being sunset by the user
2. MindGraph provides a strict superset of Zep's capabilities
3. The user owns both MindGraph and MiroFish, allowing tight iteration on SDK fixes
4. Integration testing showed 36/36 SDK tests passing — sufficient confidence for direct cutover

---

## Files Modified

### New/Rewritten
- `app/utils/mindgraph_client.py` — Complete rewrite from raw `requests` to `mindgraph-sdk` wrapper

### Modified (1-line changes)
- `app/services/graph_builder.py` — `client._request(...)` → `client.get_job(job_id)`

### Modified (comment/docstring updates)
- `app/services/simulation_manager.py` — "Zep" → "MindGraph" in 5 comments
- `app/services/simulation_runner.py` — "Zep" → "MindGraph" in 5 comments
- `app/services/report_agent.py` — "Zep" → "MindGraph" in module docstring
- `app/services/graph_tools.py` — "Zep" → "MindGraph" in 1 comment
- `app/services/ontology_generator.py` — "Zep" → "MindGraph" in 1 comment
- `app/services/oasis_profile_generator.py` — "Zep" → "MindGraph" in 1 comment
- `app/api/simulation.py` — "Zep" → "MindGraph" in 4 comments

### Config
- `pyproject.toml` — `mindgraph-sdk>=0.1.3,<0.2`

### Zero Changes Required
- `app/services/entity_reader.py` — Public API unchanged
- `app/services/graph_memory_updater.py` — Only `decay_project_salience` → `decay_salience` rename + `agent_id` → `project_id`
- `app/__init__.py` — No changes
- `app/config.py` — Already had MindGraph config from initial commit
- All frontend files — API contract unchanged

---

## Risk Assessment (Post-Migration)

| Original Risk | Actual Outcome |
|---|---|
| MindGraph API latency | Not a problem — async writes + batch buffering work well |
| MindGraph API unavailability | Acceptable risk — no fallback, but MindGraph Cloud has been stable |
| Entity resolution mismatch | N/A — no Zep to conflict with |
| Epistemic write volume | Managed — content filtering + batch size tuning |
| Report quality regression | Cannot assess yet — no end-to-end test with real LLM |
| Two-backend complexity | Eliminated — single backend |

---

## Remaining Work

See `docs/mindgraph-integration-analysis.md` for current status and remaining items.
