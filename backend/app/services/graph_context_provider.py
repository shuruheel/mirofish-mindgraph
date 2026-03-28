"""
Graph Context Provider for simulation agents.

Provides graph context to OASIS agents during simulation via two mechanisms:
1. Semantic retrieval: One query per round against the full knowledge graph,
   based on the posts all agents see in their feed. The result is shared —
   all agents in the same round get the same "Relevant knowledge" block,
   since they observe the same posts. Retrieved once per round via a
   background thread at round start.
2. Simulation awareness: Per-agent cached lookups for related agents' recent
   activity and simulation-wide decisions.

Caching layers:
- Session cache: Entity nodes + edges + relationship map loaded once (~2-3s)
- Simulation cache: Journal/Decision nodes refreshed every N rounds (~1s)
- Round cache: Semantic retrieval result (one per round, shared across agents)
"""

import json as _json
import logging
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
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

# Background thread for round-level retrieval
_retrieval_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="graph-ctx")


class GraphContextProvider:
    """
    Provides graph context to agents during OASIS simulation.

    Usage:
        provider = GraphContextProvider(client, project_id, sim_dir)
        provider.warm_cache()  # Once at sim start

        # At round start (before env.step):
        provider.start_round_retrieval(round_num, observation_text)

        # Per agent (inside to_text_prompt monkey-patch):
        context = provider.get_agent_context("Deng Xiaoping", round_num)

        # Every N rounds:
        provider.refresh_simulation_nodes()
    """

    def __init__(self, client, project_id: str, sim_dir: str = ""):
        self._client = client
        self._project_id = project_id
        self._sim_dir = sim_dir

        # Dedicated retrieval client with moderate timeout (initialized lazily)
        self._retrieval_client = None

        # Session-level caches (book knowledge — loaded once)
        self._entity_nodes: Dict[str, Dict] = {}       # name → node dict
        self._entity_uid_map: Dict[str, str] = {}       # name → uid
        self._entity_edges: Dict[str, List[Dict]] = {}  # uid → [edge dicts]
        self._relationship_map: Dict[str, Set[str]] = defaultdict(set)
        self._claims_by_entity: Dict[str, List[str]] = defaultdict(list)

        # Simulation-level cache (refreshed between rounds)
        self._simulation_journals: List[Dict] = []
        self._simulation_decisions: List[Dict] = []
        self._journals_by_agent: Dict[str, List[Dict]] = defaultdict(list)

        # Round-level semantic cache (one result shared across all agents)
        self._round_semantic_block: str = ""      # Formatted knowledge block
        self._round_semantic_round: int = -1      # Which round this result is for
        self._round_retrieval_future: Optional[Future] = None

        # Per-agent round cache (semantic block + per-agent supplements)
        self._round_cache: Dict[str, str] = {}

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
        entity_names_lower = {name: name.lower() for name in self._entity_nodes
                              if len(name) >= 4}
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

            uid_to_name = {v: k for k, v in self._entity_uid_map.items()}
            for edge in edges:
                from_uid = edge.get("from_uid", "")
                to_uid = edge.get("to_uid", "")
                edge_type = edge.get("edge_type", "")
                if not isinstance(edge_type, str):
                    edge_type = str(edge_type) if edge_type else ""

                self._entity_edges.setdefault(from_uid, []).append(edge)

                from_name = uid_to_name.get(from_uid)
                to_name = uid_to_name.get(to_uid)
                if from_name and to_name and edge_type in RELATIONSHIP_EDGE_TYPES:
                    self._relationship_map[from_name].add(to_name)
                    self._relationship_map[to_name].add(from_name)

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
        """Refresh simulation-created nodes (Journal, Decision)."""
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
        """Clear per-round caches. Called at start of each round."""
        self._round_cache.clear()

    # =========================================================================
    # Round-level semantic retrieval (one call per round, shared across agents)
    # =========================================================================

    def start_round_retrieval(self, round_num: int, observation_text: str):
        """
        Kick off semantic retrieval for this round in a background thread.

        Called once per round before env.step(). The observation_text is the
        shared feed that all agents see (from any agent's to_text_prompt).
        The retrieval result is cached and served to all agents in the round.

        Args:
            round_num: Current round number
            observation_text: Any agent's observation text (posts are shared)
        """
        if not self._warmed:
            return

        # Already have results for this round
        if self._round_semantic_round == round_num:
            return

        post_content = self._extract_post_content(observation_text)
        if not post_content:
            self._round_semantic_block = ""
            self._round_semantic_round = round_num
            logger.info(f"Round {round_num}: no posts found, skipping retrieval")
            return

        # Submit retrieval to background thread
        self._round_retrieval_future = _retrieval_executor.submit(
            self._do_round_retrieval, round_num, post_content
        )
        logger.info(f"Round {round_num}: semantic retrieval started in background")

    def _do_round_retrieval(self, round_num: int, post_content: str):
        """Execute the semantic retrieval (runs in background thread)."""
        query = post_content[:500]  # Concise query for the embedding model

        try:
            if self._retrieval_client is None:
                from mindgraph import MindGraph
                self._retrieval_client = MindGraph(
                    self._client.base_url,
                    api_key=self._client.api_key,
                    timeout=30.0,
                )

            t0 = time.time()
            result = self._retrieval_client.retrieve_context(
                query=query,
                k=3,
                depth=1,
                include_chunks=True,
            )
            elapsed = time.time() - t0

            block = self._format_retrieval_result(result, MAX_CONTEXT_CHARS)
            self._round_semantic_block = block
            self._round_semantic_round = round_num
            logger.info(
                f"Round {round_num}: semantic retrieval completed in {elapsed:.1f}s "
                f"({len(block)} chars)"
            )

        except Exception as e:
            self._round_semantic_block = ""
            self._round_semantic_round = round_num
            logger.warning(f"Round {round_num}: semantic retrieval failed: {e}")

    def _wait_for_round_retrieval(self):
        """Block until the current round's retrieval is done (if any)."""
        if self._round_retrieval_future is not None:
            try:
                self._round_retrieval_future.result(timeout=35)
            except Exception:
                pass
            self._round_retrieval_future = None

    # =========================================================================
    # Per-agent context assembly
    # =========================================================================

    def get_agent_context(self, agent_name: str, round_num: int,
                          observation_text: str = "") -> str:
        """
        Get graph context for one agent.

        The semantic retrieval block is shared across all agents in the round
        (one API call, cached). Per-agent supplements (related agents' activity,
        recent decisions) are added on top.

        Args:
            agent_name: The agent's entity name
            round_num: Current simulation round
            observation_text: Ignored (kept for API compatibility). Retrieval
                is triggered by start_round_retrieval() at round start.

        Returns:
            Context string, or empty string if no context
        """
        if not self._warmed:
            return ""

        cache_key = f"{agent_name}:{round_num}"
        if cache_key in self._round_cache:
            return self._round_cache[cache_key]

        # If retrieval was started for this round, wait for it
        if self._round_semantic_round != round_num:
            # Retrieval wasn't started yet — trigger it now from observation_text
            if observation_text:
                self.start_round_retrieval(round_num, observation_text)
            self._wait_for_round_retrieval()
        elif self._round_retrieval_future is not None:
            self._wait_for_round_retrieval()

        parts = []
        char_budget = MAX_CONTEXT_CHARS

        # 1. Shared semantic retrieval block (same for all agents this round)
        if self._round_semantic_block:
            parts.append(self._round_semantic_block)
            char_budget -= len(self._round_semantic_block)

        # 2. Related agents' recent activity (per-agent, from simulation data)
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

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _extract_post_content(observation_text: str) -> str:
        """
        Extract user_name + content pairs from the OASIS observation prompt.

        The observation contains: groups env → posts JSON → action prompt.
        We anchor on "you see some posts" to find the posts array.
        """
        marker = "you see some posts"
        marker_pos = observation_text.find(marker)
        if marker_pos < 0:
            return ""

        posts_section = observation_text[marker_pos + len(marker):]
        bracket_start = posts_section.find("[")
        if bracket_start < 0:
            return ""

        # Find the matching closing bracket
        depth = 0
        bracket_end = -1
        for i in range(bracket_start, len(posts_section)):
            if posts_section[i] == "[":
                depth += 1
            elif posts_section[i] == "]":
                depth -= 1
                if depth == 0:
                    bracket_end = i
                    break

        if bracket_end < 0:
            return ""

        json_str = posts_section[bracket_start:bracket_end + 1]
        try:
            posts = _json.loads(json_str)
        except (_json.JSONDecodeError, ValueError):
            return ""

        lines = []
        for post in posts:
            user = post.get("user_name", "")
            content = post.get("content", "")
            if content:
                lines.append(f"{user}: {content}" if user else content)

        return "\n".join(lines)

    @staticmethod
    def _format_retrieval_result(result: Dict[str, Any], char_budget: int) -> str:
        """Format retrieve_context response into a text block."""
        graph_data = result.get("graph", {})
        nodes = graph_data.get("nodes", [])
        edges = graph_data.get("edges", [])
        chunks = result.get("chunks", [])

        knowledge_lines = []
        seen = set()

        for edge in edges:
            label = edge.get("label", "")
            if label and label not in seen:
                knowledge_lines.append(f"- {label[:200]}")
                seen.add(label)

        for node in nodes:
            summary = node.get("summary", "") or node.get("label", "")
            node_type = node.get("node_type", "")
            if summary and summary not in seen:
                prefix = f"[{node_type}] " if node_type else ""
                knowledge_lines.append(f"- {prefix}{summary[:200]}")
                seen.add(summary)

        for chunk in chunks:
            content = chunk.get("content", "") or chunk.get("text", "")
            if content and content[:80] not in seen:
                knowledge_lines.append(f"- [Source] {content[:250]}")
                seen.add(content[:80])

        if not knowledge_lines:
            return ""

        block = "## Relevant knowledge\n"
        for line in knowledge_lines:
            candidate = block + line + "\n"
            if len(candidate) > char_budget:
                break
            block = candidate

        return block.rstrip()

    def get_related_agents(self, agent_name: str) -> Set[str]:
        """Get names of agents connected via graph relationships."""
        return self._relationship_map.get(agent_name, set())

    def get_relationship_map(self) -> Dict[str, Set[str]]:
        """Return the full relationship map for agent selection logic."""
        return dict(self._relationship_map)
