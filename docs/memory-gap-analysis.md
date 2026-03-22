# Memory Gap Analysis: MiroFish → MindGraph

**Date:** 2026-03-21
**Basis:** Codebase inspection documented in `docs/current-architecture.md`

---

## 1. What MiroFish Currently Represents Well

### Structured Knowledge Graph (via Zep)
- **Entity-relationship extraction** from seed documents is well-architected. The LLM-generated ontology (10 entity types, 6-10 edge types) creates a typed graph that goes beyond flat text search.
- **Temporal edges** — Zep's Graphiti engine maintains `valid_at`, `invalid_at`, and `expired_at` timestamps on edges, giving the graph a temporal dimension. The `PanoramaSearch` tool already exploits this to distinguish "active" vs. "historical" facts.
- **Text-to-graph pipeline** is automated end-to-end: upload → chunk → ingest → wait for processing → queryable graph.

### Agent Profiles
- Rich, LLM-generated personas grounded in graph data. Each agent has bio, persona narrative, demographics, interests, sentiment, stance, and influence weight.
- Profiles are linked to source entities via `source_entity_uuid`, maintaining provenance.

### Simulation Configuration
- LLM-generated simulation parameters (time patterns, activity levels, initial events) are well-structured and stored as typed dataclasses.

### Activity Logging
- Every agent action is logged as structured JSONL with platform, agent identity, action type, and full action arguments (including referenced content).

---

## 2. What Is Implicit, Flattened, or Mixed Together

### World Facts vs. Agent Beliefs
**Current state:** Both seed document facts and simulation-generated agent activities are written to the **same Zep graph** as undifferentiated text episodes. When agent "张三" posts "我认为这个政策是错误的", Zep extracts entities and edges from that text and merges them into the same graph as facts from the source documents.

**Problem:** There is no way to distinguish:
- A fact from a source document ("The university was founded in 1893")
- An agent's stated opinion during simulation ("This policy will fail")
- A simulated social interaction ("Agent A liked Agent B's post")

They all become edges in the same graph with the same ontological status.

### Private Intent vs. Public Statements
**Current state:** Agent profiles contain stance, sentiment_bias, and persona descriptions — but these are static configuration. During simulation, only **public actions** are logged (posts, likes, comments). There is no record of:
- What an agent wanted to achieve (goals)
- What tradeoffs they considered before acting (deliberation)
- Why they chose one action over another (decision traces)

### Goals vs. Plans vs. Actions
**Current state:** The `simulation_config_generator.py` produces `EventConfig` with initial posts and scheduled events, and `AgentActivityConfig` with stance and influence weight. But these are **simulation parameters**, not agent-level goals or plans. During simulation, only the executed actions are recorded — the agent's internal planning (done by OASIS/LLM) is opaque.

### Raw Activity vs. Epistemic Claims
**Current state:** When an agent posts "校方的回应缺乏诚意", this is stored as activity text in Zep. Zep may extract it as an edge, but it has no special epistemic status — it's just another fact in the graph. There's no:
- Confidence score on the claim
- Evidence linking (what prompted this belief?)
- Warrant structure (why does this follow?)
- Tracking of belief revision (did the agent change their mind?)

### Simulation Telemetry vs. Cognitive Memory
**Current state:** The `ZepGraphMemoryUpdater` converts every non-DO_NOTHING action to natural language and writes it to Zep. This means "Agent A liked Agent B's post about X" is treated the same as "Agent A believes X is true." Liking a post is telemetry; forming a belief is cognition. Currently these are conflated.

---

## 3. What MiroFish Fails to Model Explicitly

### Confidence and Warrant Structure
No mechanism to represent how strongly an agent (or the system) holds a claim, what evidence supports it, or whether that evidence is strong or weak. The ReportAgent can search for facts but cannot query "which claims have the weakest support?" or "where do agents contradict each other?"

### Cross-Agent Belief Visibility
No way to ask "Which agents believe X?" or "How do Agent A's beliefs differ from Agent B's?" All beliefs are mixed into one graph — there's no per-agent belief partition.

### Decision Traces and Deliberation History
No record of why an agent chose to post vs. like vs. do nothing. OASIS handles this internally, but MiroFish doesn't capture or store the reasoning.

### Argument Structure
No representation of claims, evidence, rebuttals, or inference chains. The ReportAgent sometimes finds contradictory facts, but there's no structured way to query "all arguments for/against prediction X."

### Calibration and Learning Across Runs
No mechanism to compare predictions across simulation runs, track which predictions were accurate, or use past results to improve future simulations. Each simulation is a standalone event.

### Version History of Evolving Beliefs
Zep has temporal edges (`valid_at`/`invalid_at`), but there's no structured belief revision tracking. If an agent changes their stance mid-simulation, this might show up as a new edge, but there's no explicit "supersedes" relationship linking old and new positions.

---

## 4. Where MindGraph Would Meaningfully Improve the System

### Reality Layer — Structured Seed Knowledge (High Value)
**Gap addressed:** Seed material facts mixed with simulation activities.

MindGraph's Reality layer (`Source`, `Snippet`, `Entity`, `Observation`) can cleanly separate:
- Source documents → `Source` nodes
- Extracted text passages → `Snippet` nodes
- Identified entities → `Entity` nodes with aliases and resolution
- Factual observations → `Observation` nodes

**Specific gain:** Entity resolution via `/reality/entity` (alias, fuzzy_resolve, merge) could stabilize the known ontology generation fragility in `graph_builder.py`. Currently, if Zep and the LLM disagree on entity boundaries, there's no reconciliation mechanism.

### Epistemic Layer — Structured Beliefs and Arguments (High Value)
**Gap addressed:** Agent opinions treated as world facts; no confidence/warrant structure.

MindGraph's Epistemic layer maps directly to the most important gap:
- Agent posts expressing opinions → `Claim` nodes with confidence scores
- Supporting evidence → `Evidence` linked to claims
- Contradictions between agents → `Contradicts`/`Refutes` edges
- Hypotheses about outcomes → `Hypothesis` nodes
- Emergent patterns → `Pattern` and `Mechanism` nodes

**Specific gains:**
- ReportAgent can use `/retrieve` with `unresolved_contradictions` and `weak_claims` pre-built queries
- Belief evolution is tracked via `/evolve` version history
- Report quality improves because the agent can query structured argument graphs, not just keyword-matched facts

### Memory Layer — Simulation Sessions and Traces (High Value)
**Gap addressed:** No structured simulation lifecycle; telemetry mixed with cognitive content.

MindGraph's Memory layer provides:
- `Session` nodes → Map to simulation runs (open/close)
- `Trace` entries → Per-round state snapshots
- `Journal` entries → Agent reflections (if generated)
- `Summary` nodes → Post-simulation distillation via `/memory/distill`
- Salience decay via `/evolve` → Natural forgetting between rounds

**Specific gain:** The `ZepGraphMemoryUpdater`'s batch-text-to-graph pattern can be replaced with structured Memory writes, giving each simulation run a clean session boundary.

### Retrieval Improvements (High Value)
**Gap addressed:** ReportAgent limited to keyword/semantic search on flat fact list.

MindGraph's `/retrieve` and `/traverse` endpoints offer:
- Pre-built queries: `active_goals`, `open_questions`, `weak_claims`, `unresolved_contradictions`, `pending_approvals`
- Graph traversal: `chain`, `neighborhood`, `path`, `subgraph`
- Hybrid search: text + semantic + graph-structural

**Specific gain:** The ReportAgent's tool suite (InsightForge, PanoramaSearch, QuickSearch) can be augmented with MindGraph traverse queries that follow reasoning chains, find contradiction clusters, and trace belief evolution — none of which are possible with Zep's flat search.

---

## 5. Where MindGraph Would Add Complexity Without Enough Benefit

### Intent Layer — Agent Goals and Decisions (Low Value Initially)
MindGraph's Intent layer (`Goal`, `Decision`, `Option`, `Constraint`) models agent-level planning. However:
- OASIS handles agent decision-making internally
- MiroFish doesn't have access to agent reasoning traces (only actions)
- Retrofitting intent tracking requires intercepting the OASIS agent loop, which is explicitly out of scope

**Recommendation:** Skip for Phase 1. Revisit only if the product moves toward cognitive simulation (ADR-001 Option B).

### Agent Layer — Plans, Governance, Safety Budgets (Low Value)
MindGraph's Agent layer (`Task`, `Plan`, `Policy`, `SafetyBudget`, `Execution`) is designed for autonomous agent governance. MiroFish agents are simulated personas in OASIS, not autonomous agents that need governance.

**Recommendation:** Skip entirely unless MiroFish adds autonomous agent capabilities beyond OASIS.

### Action Layer — Procedures and Risk Assessment (Low Value)
MindGraph's Action layer (`Flow`, `FlowStep`, `RiskAssessment`) could model god's-eye interventions, but MiroFish currently handles interventions through simulation config (initial posts, scheduled events). The overhead of formalizing this in MindGraph is not justified.

**Recommendation:** Skip for Phase 1. Could be useful later for structured intervention modeling.

---

## 6. Summary: Priority Matrix

| MindGraph Layer | Value to MiroFish | Implementation Effort | Priority |
|----------------|-------------------|-----------------------|----------|
| Reality | High — separates seed facts from sim activity | Medium — new write path for graph construction | Phase 1 |
| Epistemic | High — structured beliefs, contradictions, arguments | Medium-High — needs write granularity decisions | Phase 1 |
| Memory | High — session boundaries, traces, decay | Medium — maps cleanly to existing patterns | Phase 1 |
| Intent | Low — OASIS handles agent decisions internally | High — requires OASIS integration changes | Phase 3+ |
| Action | Low — existing config handles interventions | Medium | Phase 3+ |
| Agent | Low — no autonomous agents to govern | High | Not planned |
| Retrieval/Traverse | High — immediate ReportAgent improvement | Low-Medium — adapter over existing tools | Phase 1 |
