"""
Graph Context Provider for simulation agents.

Provides graph context to OASIS agents during simulation via two mechanisms:
1. Semantic retrieval: Per-turn queries against the full knowledge graph based
   on what the agent is currently observing (posts, discussions). This surfaces
   topically relevant knowledge — claims, evidence, entity relationships — that
   helps agents make informed decisions.
2. Simulation awareness: Cached lookups for related agents' recent activity
   and simulation-wide decisions.

Caching layers:
- Session cache: Entity nodes + edges + relationship map loaded once (~2-3s)
- Simulation cache: Journal/Decision nodes refreshed every N rounds (~1s)
- Round cache: Per-agent-per-round dedup for semantic queries (~0ms)
"""

import logging
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("mirofish.graph_context_provider")

# Relationship edge types that indicate agents should interact
RELATIONSHIP_EDGE_TYPES = {
    "AffiliatedWith", "WorksFor", "PartOf", "Allies", "Opposes",
    "RelatedTo", "Supports", "About",
}

# Max tokens of context to inject per agent per round
MAX_CONTEXT_TOKENS = 1500
# Max characters (rough proxy for tokens — ~3 chars per token for mixed en/zh)
MAX_CONTEXT_CHARS = MAX_CONTEXT_TOKENS * 3


class GraphContextProvider:
    """
    Provides graph context to agents during OASIS simulation.

    Usage:
        provider = GraphContextProvider(client, project_id, sim_dir)
        provider.warm_cache()  # Once at sim start

        # Each round:
        provider.invalidate_round_cache()
        context = provider.get_agent_context("Deng Xiaoping", round_num=3)

        # Every N rounds:
        provider.refresh_simulation_nodes()
    """

    def __init__(self, client, project_id: str, sim_dir: str = ""):
        """
        Args:
            client: MindGraphClient instance
            project_id: Project ID (used as agent_id for simulation-created nodes)
            sim_dir: Simulation directory for optional disk caching
        """
        self._client = client
        self._project_id = project_id
        self._sim_dir = sim_dir

        # Session-level caches (book knowledge — loaded once)
        self._entity_nodes: Dict[str, Dict] = {}       # name → node dict
        self._entity_uid_map: Dict[str, str] = {}       # name → uid
        self._entity_edges: Dict[str, List[Dict]] = {}  # uid → [edge dicts]
        self._relationship_map: Dict[str, Set[str]] = defaultdict(set)  # name → related names
        self._claims_by_entity: Dict[str, List[str]] = defaultdict(list)  # name → [claim texts]

        # Simulation-level cache (refreshed between rounds)
        self._simulation_journals: List[Dict] = []   # Journal nodes from simulation
        self._simulation_decisions: List[Dict] = []  # Decision nodes from simulation
        self._journals_by_agent: Dict[str, List[Dict]] = defaultdict(list)  # agent_name → journals

        # Per-round cache
        self._round_cache: Dict[str, str] = {}  # cache_key → context string

        self._warmed = False

    def warm_cache(self):
        """
        Load session-level caches. Called once at simulation start.
        Fetches all Entity nodes and their edges via batch APIs.
        """
        t0 = time.time()

        # 1. Load all Entity nodes from the full graph (book knowledge)
        try:
            all_nodes = self._client.list_all_graph_nodes(max_items=3000)
        except Exception as e:
            logger.warning(f"Failed to load graph nodes: {e}")
            all_nodes = []

        # Pass 1: Index all nodes by name and collect entity UIDs
        entity_uids = []
        claim_nodes = []
        for node in all_nodes:
            uid = node.get("uid", "")
            label = node.get("label", "")
            node_type = node.get("node_type", "")

            if not uid or not label:
                continue

            self._entity_nodes[label] = node
            self._entity_uid_map[label] = uid

            if node_type == "Entity":
                entity_uids.append(uid)
            elif node_type == "Claim":
                claim_nodes.append(node)

        # Pass 2: Index claims against the complete entity name set
        # (must be after pass 1 so all entity names are known)
        entity_names_lower = {name: name.lower() for name in self._entity_nodes
                              if len(name) >= 4}  # Skip short names to avoid false matches
        for node in claim_nodes:
            summary = node.get("summary", "") or node.get("label", "")
            props = node.get("props", {})
            content = props.get("content", "") if isinstance(props, dict) else ""
            claim_text = content or summary
            if claim_text:
                claim_lower = claim_text.lower()
                for ename, ename_lower in entity_names_lower.items():
                    if ename_lower in claim_lower:
                        self._claims_by_entity[ename].append(claim_text[:200])

        # 2. Load edges between entities via batch API
        if entity_uids:
            try:
                edges = self._client.get_edges_batch(entity_uids)
            except Exception as e:
                logger.warning(f"Failed to load entity edges: {e}")
                edges = []

            # Build lookup and relationship map
            uid_to_name = {v: k for k, v in self._entity_uid_map.items()}
            for edge in edges:
                from_uid = edge.get("from_uid", "")
                to_uid = edge.get("to_uid", "")
                edge_type = edge.get("edge_type", "")
                # edge_type can be a dict from some API responses — normalize to string
                if not isinstance(edge_type, str):
                    edge_type = str(edge_type) if edge_type else ""

                self._entity_edges.setdefault(from_uid, []).append(edge)

                # Build relationship map for entities connected by meaningful edge types
                from_name = uid_to_name.get(from_uid)
                to_name = uid_to_name.get(to_uid)
                if from_name and to_name and edge_type in RELATIONSHIP_EDGE_TYPES:
                    self._relationship_map[from_name].add(to_name)
                    self._relationship_map[to_name].add(from_name)

                # Also track "About" edges (Observation/Claim about Entity)
                if edge_type == "About" and to_name:
                    from_node = self._entity_nodes.get(uid_to_name.get(from_uid, ""), {})
                    if from_node.get("node_type") in ("Observation", "Claim"):
                        summary = from_node.get("summary", "")
                        if summary:
                            self._claims_by_entity[to_name].append(summary[:200])

        # 3. Load initial simulation nodes
        self.refresh_simulation_nodes()

        elapsed = time.time() - t0
        self._warmed = True
        logger.info(
            f"GraphContextProvider cache warmed in {elapsed:.1f}s: "
            f"{len(self._entity_nodes)} entities, "
            f"{sum(len(v) for v in self._entity_edges.values())} edges, "
            f"{len(self._relationship_map)} relationship entries, "
            f"{len(self._simulation_journals)} simulation journals"
        )

    def refresh_simulation_nodes(self):
        """
        Refresh simulation-created nodes (Journal, Decision).
        Called between rounds to pick up new data from prior rounds.
        """
        try:
            sim_nodes = self._client.list_all_nodes(
                project_id=self._project_id, max_items=2000
            )
        except Exception as e:
            logger.debug(f"Failed to refresh simulation nodes: {e}")
            return

        self._simulation_journals = []
        self._simulation_decisions = []
        self._journals_by_agent = defaultdict(list)

        for node in sim_nodes:
            node_type = node.get("node_type", "")
            props = node.get("props", {}) if isinstance(node.get("props"), dict) else {}

            if node_type == "Journal":
                journal_type = props.get("journal_type", "")
                if journal_type == "simulation_post":
                    self._simulation_journals.append(node)
                    # Extract agent name from content: "Agent Name: content..."
                    content = props.get("content", "")
                    colon_idx = content.find(":")
                    if colon_idx > 0:
                        agent_name = content[:colon_idx].strip()
                        self._journals_by_agent[agent_name].append(node)
            elif node_type == "Decision":
                self._simulation_decisions.append(node)

        logger.debug(
            f"Simulation nodes refreshed: {len(self._simulation_journals)} journals, "
            f"{len(self._simulation_decisions)} decisions"
        )

    def invalidate_round_cache(self):
        """Clear per-round query cache. Called at start of each round."""
        self._round_cache.clear()

    def get_agent_context(self, agent_name: str, round_num: int,
                          observation_text: str = "") -> str:
        """
        Get graph context for one agent based on their current observation.

        Uses semantic retrieval against the full knowledge graph to find
        topically relevant knowledge for what the agent is currently seeing,
        then supplements with simulation activity awareness.

        Context includes:
        1. Semantically relevant knowledge from the graph (topic-aware)
        2. Related agents' recent simulation activity
        3. Recent simulation-wide decisions

        Args:
            agent_name: The agent's entity name
            round_num: Current simulation round
            observation_text: The agent's current observation (posts they see).
                Used as the basis for semantic retrieval queries.

        Returns:
            Context string (~500 tokens max), or empty string if no context
        """
        if not self._warmed:
            return ""

        cache_key = f"{agent_name}:{round_num}"
        if cache_key in self._round_cache:
            return self._round_cache[cache_key]

        parts = []
        char_budget = MAX_CONTEXT_CHARS

        # 1. Semantic retrieval from the full knowledge graph
        if observation_text:
            semantic_block = self._retrieve_semantic_context(
                agent_name, observation_text, char_budget
            )
            if semantic_block:
                parts.append(semantic_block)
                char_budget -= len(semantic_block)

        # 2. Related agents' recent activity (from simulation data)
        related = self._relationship_map.get(agent_name, set())
        if related and char_budget > 200:
            activity_lines = []
            for related_name in list(related)[:5]:
                journals = self._journals_by_agent.get(related_name, [])
                if journals:
                    latest = journals[-1]
                    props = latest.get("props", {})
                    content = props.get("content", "")
                    colon_idx = content.find(":")
                    if colon_idx > 0:
                        content = content[colon_idx + 1:].strip()
                    if content:
                        activity_lines.append(
                            f"- {related_name}: {content[:150]}"
                        )
            if activity_lines:
                block = "## Related actors' recent activity\n" + "\n".join(activity_lines)
                if len(block) <= char_budget:
                    parts.append(block)
                    char_budget -= len(block)

        # 3. Recent simulation-wide decisions (if any)
        if self._simulation_decisions and char_budget > 150:
            recent_decisions = self._simulation_decisions[-3:]
            decision_lines = []
            for d in recent_decisions:
                label = d.get("label", "")
                if label:
                    decision_lines.append(f"- {label[:100]}")
            if decision_lines:
                block = "## Recent decisions in the simulation\n" + "\n".join(decision_lines)
                if len(block) <= char_budget:
                    parts.append(block)

        context = "\n\n".join(parts)
        self._round_cache[cache_key] = context
        return context

    @staticmethod
    def _extract_post_content(observation_text: str) -> str:
        """
        Extract meaningful text content from the OASIS observation prompt.

        The observation is structured as:
            <groups env>
            After refreshing, you see some posts <JSON array of post objects>
            pick one you want to perform action...

        The JSON posts use indent=4 formatting, so raw truncation wastes most
        of the budget on JSON syntax. This method extracts just the post
        content fields and user names for a focused semantic query.
        """
        import json as _json

        # Find the JSON array in the observation
        bracket_start = observation_text.find("[")
        bracket_end = observation_text.rfind("]")
        if bracket_start < 0 or bracket_end <= bracket_start:
            # No JSON found — use raw text (trimmed of boilerplate)
            return observation_text[:1000]

        json_str = observation_text[bracket_start:bracket_end + 1]
        try:
            posts = _json.loads(json_str)
        except (_json.JSONDecodeError, ValueError):
            return observation_text[:1000]

        # Extract user_name + content from each post
        lines = []
        for post in posts:
            user = post.get("user_name", "")
            content = post.get("content", "")
            if content:
                lines.append(f"{user}: {content}" if user else content)

        return "\n".join(lines) if lines else observation_text[:1000]

    def _retrieve_semantic_context(self, agent_name: str,
                                   observation_text: str,
                                   char_budget: int) -> str:
        """
        Query the full knowledge graph for content relevant to the agent's
        current observation. Returns a formatted text block.

        The query combines the agent's identity with the substance of the
        posts they're seeing, so retrieval is both agent-aware and topic-aware.
        """
        # Extract post content from the structured observation
        post_content = self._extract_post_content(observation_text)

        # Build query: agent identity + post substance (capped for embedding model)
        query = f"{agent_name}: {post_content[:1500]}"

        try:
            t0 = time.time()
            result = self._client.retrieve_context(
                query=query,
                project_id=None,  # Unscoped — search the full knowledge graph
                k=5,
                depth=1,
                include_chunks=True,  # Include source text passages for richer context
            )
            elapsed = time.time() - t0
            logger.debug(f"Semantic retrieval for {agent_name}: {elapsed:.2f}s")

            graph_data = result.get("graph", {})
            nodes = graph_data.get("nodes", [])
            edges = graph_data.get("edges", [])
            chunks = result.get("chunks", [])

            knowledge_lines = []
            seen = set()

            # Edges capture relationships as natural language facts
            for edge in edges:
                label = edge.get("label", "")
                if label and label not in seen:
                    knowledge_lines.append(f"- {label[:200]}")
                    seen.add(label)

            # Nodes provide entity context and claim summaries
            for node in nodes:
                summary = node.get("summary", "") or node.get("label", "")
                node_type = node.get("node_type", "")
                if summary and summary not in seen:
                    prefix = f"[{node_type}] " if node_type else ""
                    knowledge_lines.append(f"- {prefix}{summary[:200]}")
                    seen.add(summary)

            # Chunks provide original source text passages
            for chunk in chunks:
                content = chunk.get("content", "") or chunk.get("text", "")
                if content and content[:80] not in seen:
                    knowledge_lines.append(f"- [Source] {content[:250]}")
                    seen.add(content[:80])

            if not knowledge_lines:
                return ""

            # Cap to fit within budget
            block = "## Relevant knowledge\n"
            for line in knowledge_lines:
                candidate = block + line + "\n"
                if len(candidate) > char_budget:
                    break
                block = candidate

            return block.rstrip()

        except Exception as e:
            logger.debug(f"Semantic retrieval failed for {agent_name}: {e}")
            # Fallback: use cached claims about this agent (static, but better than nothing)
            claims = self._claims_by_entity.get(agent_name, [])
            if claims:
                sorted_claims = sorted(claims, key=len)[:3]
                claim_lines = [f"- {c[:120]}" for c in sorted_claims]
                return "## Key knowledge about you\n" + "\n".join(claim_lines)
            return ""

    def get_related_agents(self, agent_name: str) -> Set[str]:
        """
        Get names of agents connected to this one via graph relationships.
        Uses the cached relationship map (session-level, O(1) lookup).
        """
        return self._relationship_map.get(agent_name, set())

    def get_relationship_map(self) -> Dict[str, Set[str]]:
        """Return the full relationship map for agent selection logic."""
        return dict(self._relationship_map)
