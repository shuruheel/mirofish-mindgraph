# ADR-005: Event Normalization Schema

**Status:** Proposed
**Date:** 2026-03-21
**Depends on:** ADR-004

---

## Problem

Raw OASIS actions (from `actions.jsonl`) and Zep graph data are in different formats. MindGraph expects its own API format. We need an intermediate representation that decouples MiroFish from both OASIS internals and MindGraph's API.

## Options Considered

### Option A: Typed Python dataclasses (recommended)
Define a `SimulationEvent` hierarchy as Python dataclasses. Each event type maps to a specific MindGraph target.

### Option B: Generic dict-based events
Use untyped dicts with a `type` field. Flexible but no compile-time safety.

### Option C: Protocol buffer / Avro schema
Formal serialization schema. Overkill for an in-process boundary.

## Decision

**Option A: Typed Python dataclasses.**

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List

class EventType(str, Enum):
    # Reality events (from seed material)
    SOURCE_INGESTED = "source_ingested"
    ENTITY_EXTRACTED = "entity_extracted"
    RELATIONSHIP_EXTRACTED = "relationship_extracted"

    # Epistemic events (from simulation)
    CLAIM_MADE = "claim_made"
    EVIDENCE_CITED = "evidence_cited"
    BELIEF_REVISED = "belief_revised"

    # Memory events (lifecycle)
    SESSION_OPENED = "session_opened"
    SESSION_CLOSED = "session_closed"
    ROUND_STARTED = "round_started"
    ROUND_ENDED = "round_ended"

    # Trace events (lightweight telemetry)
    SOCIAL_ACTION = "social_action"

class ConfidenceLevel(str, Enum):
    LOW = "low"         # 0.3
    MEDIUM = "medium"   # 0.6
    HIGH = "high"       # 0.8
    VERY_HIGH = "very_high"  # 0.95

@dataclass
class SimulationEvent:
    """Base event — all events carry these fields."""
    event_type: EventType
    simulation_id: str
    timestamp: str
    agent_id: Optional[str] = None  # MindGraph agent_id
    platform: Optional[str] = None  # twitter / reddit
    round_num: Optional[int] = None
    metadata: dict = field(default_factory=dict)

@dataclass
class ClaimEvent(SimulationEvent):
    """Agent made a substantive statement."""
    content: str = ""
    confidence: float = 0.6
    source_action_type: str = ""  # CREATE_POST, CREATE_COMMENT, etc.
    referenced_content: Optional[str] = None  # Original post being quoted/commented on
    referenced_agent_id: Optional[str] = None

@dataclass
class SocialActionEvent(SimulationEvent):
    """Lightweight social interaction (like, follow, etc.)."""
    action_type: str = ""  # LIKE_POST, FOLLOW, etc.
    target_agent_id: Optional[str] = None
    target_content: Optional[str] = None

@dataclass
class SessionEvent(SimulationEvent):
    """Simulation lifecycle event."""
    action: str = ""  # open, close

@dataclass
class RoundEvent(SimulationEvent):
    """Round boundary marker."""
    simulated_hour: int = 0
    active_agents: int = 0
```

## Expected Consequences

- `event_normalizer.py` converts `AgentAction` → `SimulationEvent` subtype
- `mindgraph_adapter.py` converts `SimulationEvent` → MindGraph API call
- `zep_graph_memory_updater.py` is unmodified (still receives raw `AgentActivity`)
- The event schema is the contract between OASIS integration and MindGraph integration

## Performance Estimate

- **Implementation:** ~1 day for schema + normalizer
- **Runtime:** Zero cost — in-memory Python object creation
