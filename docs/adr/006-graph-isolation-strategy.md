# ADR-006: Graph Isolation Strategy

**Status:** Proposed
**Date:** 2026-03-21
**Depends on:** ADR-002

---

## Problem

Should each simulation use its own MindGraph graph, or should simulations share a persistent graph that accumulates knowledge across runs?

## Options Considered

### Option A: One graph per simulation (recommended)
Each simulation creates a fresh MindGraph graph. Seed material is projected in at the start. Simulation events are written to this graph. The graph is preserved after simulation for reporting/querying but is not shared with future simulations.

### Option B: Shared persistent graph
One MindGraph graph per project. Seed material is written once. Multiple simulations write to the same graph, with session boundaries separating runs. Cross-run queries are natural (e.g., "how did beliefs evolve across 3 simulation runs?").

### Option C: Hybrid — shared Reality, per-simulation Epistemic
Seed material (Reality layer) lives in a shared project graph. Each simulation gets its own Epistemic/Memory subgraph. Cross-run Reality queries work; Epistemic isolation prevents contamination.

## Decision

**Option A: One graph per simulation.**

Rationale:
- Matches current MiroFish behavior (each simulation is independent)
- Simplest to implement and reason about
- No risk of simulation contamination (one run's beliefs affecting another)
- MindGraph's session model still provides internal structure within a simulation
- Cross-run comparison can be done at the application level (query two graphs and compare)

Option B was rejected because MiroFish currently has no cross-run continuity, and adding it is a product decision, not an infrastructure one. If cross-run continuity becomes a goal (ADR-001 moving toward Option B), this ADR can be revisited.

Option C is architecturally clean but adds significant complexity for a feature not yet needed.

## Expected Consequences

- Each `POST /api/simulation/create` triggers a MindGraph graph creation (when enabled)
- Graph ID stored in `SimulationState` alongside Zep `graph_id`
- Graph lifecycle: created at simulation start, active during simulation, read-only after completion
- No garbage collection needed — graphs persist for querying
- Cross-run comparison: future feature, implemented by querying multiple graphs

## Performance Estimate

- **Implementation:** Minimal — graph creation is one API call
- **Storage:** One MindGraph graph per simulation (similar to current Zep pattern)
- **Operational:** No cross-graph consistency concerns
