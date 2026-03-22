# ADR-004: Epistemic Write Granularity

**Status:** Proposed
**Date:** 2026-03-21
**Depends on:** ADR-002

---

## Problem

What triggers a structured epistemic write to MindGraph vs. a lightweight telemetry trace? Writing every agent action as an epistemic claim would overwhelm MindGraph and make the graph noisy. Writing too little loses the cognitive modeling benefit.

## Options Considered

### Option A: Content-based filtering
Only actions that produce substantive text content become epistemic writes. Social actions (likes, follows) become trace entries.

| Action Type | MindGraph Target | Condition |
|-------------|-----------------|-----------|
| CREATE_POST | Epistemic (Claim) | Content length > 20 chars |
| CREATE_COMMENT | Epistemic (Claim) | Content length > 20 chars |
| QUOTE_POST | Epistemic (Claim) | Quote content > 20 chars |
| LIKE_POST | Memory (Trace) | Always |
| DISLIKE_POST | Memory (Trace) | Always |
| REPOST | Memory (Trace) | Always |
| FOLLOW | Memory (Trace) | Always |
| SEARCH_* | Memory (Trace) | Always |
| MUTE | Memory (Trace) | Always |
| DO_NOTHING | Skip | Always |

### Option B: Sampling-based filtering
Write 1 in N actions as epistemic claims. Reduces volume but loses important content randomly.

### Option C: LLM-based filtering
Pass each action to an LLM to decide if it's epistemically significant. High quality but prohibitively expensive (LLM call per action).

## Decision

**Option A: Content-based filtering.**

Rationale:
- Simple, deterministic, zero additional LLM cost
- Naturally captures the highest-value simulation content (what agents say)
- Social interactions (likes, follows) still captured as trace entries — not lost, just lower-status
- 20-char minimum filters out empty or trivial content

**Additional refinement:** Within epistemic writes, assign confidence based on:
- Agent's `influence_weight` from simulation config (0.0-1.0 → 0.3-0.9 confidence)
- Action type weight: CREATE_POST=1.0, CREATE_COMMENT=0.8, QUOTE_POST=0.9

## Expected Consequences

- Estimated epistemic writes per simulation round: 30-50% of total actions (only content-bearing ones)
- Estimated trace writes: 50-70% of total actions (social + content actions)
- Total MindGraph writes per round: roughly equal to current Zep writes (same events, structured differently)
- Graph growth: ~1 Claim node + edges per substantive post/comment/quote per agent per round

## Performance Estimate

- **Implementation:** ~2 days for the filtering logic in event_normalizer
- **Runtime cost:** Same number of API calls as Zep (batched); no LLM overhead
- **Storage growth:** Manageable — 10 agents × 10 rounds × ~3 posts/round = ~300 Claim nodes per simulation
