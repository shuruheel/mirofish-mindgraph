# ADR-003: Zep Coexistence Strategy

**Status:** Proposed
**Date:** 2026-03-21
**Depends on:** ADR-002

---

## Problem

In dual-write mode, how do Zep and MindGraph coexist? Which is authoritative for what?

## Options Considered

### Option A: Zep authoritative, MindGraph supplementary
Zep remains the source of truth for: graph construction, entity reading, profile enrichment, and basic search. MindGraph adds: structured epistemic queries, session tracking, belief evolution. If they disagree, Zep wins.

### Option B: Split authority by layer
Zep authoritative for Reality-equivalent data (entities, relationships, facts). MindGraph authoritative for Epistemic, Memory, and Intent data. Neither overrides the other in its domain.

### Option C: MindGraph authoritative, Zep as cache
MindGraph is primary. Zep serves as a fast cache for entity lookups and text search.

## Decision

**Option A: Zep authoritative, MindGraph supplementary.**

Rationale:
- Existing code depends heavily on Zep's node/edge model (13+ callsites)
- MindGraph's value is additive (structured cognition), not replacing basic graph operations
- Minimizes blast radius if MindGraph integration has issues
- Entity resolution: Zep's entity IDs remain canonical; MindGraph entities reference Zep UUIDs via metadata

## Expected Consequences

- No existing Zep queries are modified or redirected
- MindGraph nodes carry `zep_entity_uuid` in metadata for cross-reference
- ReportAgent tool selection: Zep tools for broad search, MindGraph tools for structured cognitive queries
- If we later decide to promote MindGraph to primary (ADR-002 Option B), the cross-references enable migration

## Performance Estimate

- **Implementation:** Minimal — just discipline about which system to query for what
- **Runtime:** No change to Zep path; MindGraph adds parallel queries
