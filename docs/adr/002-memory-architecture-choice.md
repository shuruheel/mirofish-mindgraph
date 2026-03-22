# ADR-002: Memory Architecture Choice

**Status:** Proposed
**Date:** 2026-03-21
**Depends on:** ADR-001

---

## Problem

How should MindGraph relate to the existing Zep infrastructure?

## Options Considered

### Option A: MindGraph as augmentation (dual-write)
MindGraph receives structured projections alongside Zep. Zep remains primary for simulation memory. MindGraph adds structured cognitive layers (Reality, Epistemic, Memory).

### Option B: MindGraph as replacement
MindGraph replaces Zep entirely. A compatibility shim translates Zep-shaped queries.

### Option C: MindGraph for retrieval only (read-side augmentation)
Post-simulation, project Zep graph data into MindGraph for structured querying. No real-time writes during simulation.

## Decision

**Option A: MindGraph as augmentation (dual-write).**

Option C was considered but rejected because it forfeits real-time epistemic tracking during simulation — the primary value proposition.

Option B was rejected due to high migration risk and the unknown performance characteristics of MindGraph under simulation load.

## Expected Consequences

- Simulation activities are written to both Zep (text episodes) and MindGraph (structured events)
- Feature flag `MINDGRAPH_WRITE_MODE` controls: `off` | `dual` | `mindgraph_only`
- ReportAgent gains MindGraph query tools alongside existing Zep tools
- If MindGraph proves superior in dual-write testing, Option B becomes a data-informed future decision

## Performance Estimate

- **Implementation:** ~1 week for dual-write infrastructure
- **Runtime:** +1 async API call per batch of 5 events
- **Latency:** <10% increase per simulation round (async, non-blocking)
