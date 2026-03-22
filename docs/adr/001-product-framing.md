# ADR-001: Product Framing

**Status:** Proposed
**Date:** 2026-03-21
**Context:** This fork integrates MindGraph into MiroFish. The product framing determines every downstream architecture decision.

---

## Problem

Is this fork primarily:

1. **A better prediction sandbox** — MiroFish with improved memory, richer reports, and more observable emergent behavior. The simulation loop is the product. MindGraph is infrastructure.

2. **A cognitive simulation engine** — A system where agents have explicit belief states, structured goals, and auditable reasoning. The cognitive model is the product. Simulation is one application.

## Options Considered

### Option A: Better Prediction Sandbox (Recommended)

MiroFish's simulation pipeline remains the core product. MindGraph augments the knowledge infrastructure to produce:
- Structured memory that distinguishes facts from beliefs from agent activities
- Richer retrieval for the ReportAgent (contradictions, weak claims, belief evolution)
- Better explainability (why did agents converge on this prediction?)
- Potential cross-run continuity (second simulation builds on first)

MindGraph is introduced incrementally. The simulation loop, OASIS integration, and existing UI remain intact. The focus is on improving report quality and memory semantics, not on building a full cognitive agent framework.

**Pros:**
- Lowest risk — preserves working pipeline
- Fastest time to measurable improvement (report quality, retrieval quality)
- MindGraph's Reality + Epistemic + Memory layers deliver immediate value
- Intent/Agent/Action layers can be adopted later if warranted

**Cons:**
- Doesn't fully leverage MindGraph's cognitive modeling capabilities
- May need re-architecture later if cognitive simulation becomes the goal

### Option B: Cognitive Simulation Engine

Agents have first-class belief states in MindGraph's Epistemic layer, explicit goals in the Intent layer, and auditable decision traces in the Agent layer. Every simulation turn writes structured cognitive state, not just activity logs. The ReportAgent queries belief evolution, goal conflicts, and decision rationale.

**Pros:**
- Maximum use of MindGraph's capabilities
- Most differentiated product
- Strongest explainability and auditability

**Cons:**
- High implementation complexity — requires deep changes to the simulation loop
- Significant per-round latency increase (epistemic writes per agent per turn)
- OASIS integration becomes much harder (need to intercept agent reasoning, not just actions)
- Risk of over-engineering before validating the basic integration works

## Decision

**Start as (A) with an architecture that enables (B).**

Design the MindGraph adapter layer cleanly enough that it can be promoted from augmentation to primary memory without a rewrite, but don't pay the full cost of (B) upfront.

Concretely:
- Phase 1-3: MindGraph as a parallel write target alongside Zep (Reality + Epistemic + Memory layers)
- Phase 4+: Evaluate whether Intent/Agent layers add enough value to justify the integration cost
- The adapter interface should be abstract enough that a future cognitive mode can be added without replacing the adapter

## Expected Consequences

**Enables:**
- Immediate improvement in report quality via structured retrieval
- Clear separation between seed facts, simulation activities, and agent beliefs
- Cross-run memory persistence if desired
- Path to cognitive simulation without a rewrite

**Risks:**
- Dual-write adds operational complexity
- Must define clear write granularity thresholds to avoid overwhelming MindGraph with trivial actions
- Zep and MindGraph may disagree on entity resolution — need clear primary/secondary designation

## Performance Estimate

- **Implementation complexity:** ~2-3 weeks for adapter layer + dual-write mode
- **Runtime cost:** 1 additional API call per batch of simulation activities (batched, async)
- **Latency impact:** Minimal if writes are async and batched (target: <10% increase per round)
