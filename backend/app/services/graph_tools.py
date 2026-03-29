"""
MindGraph retrieval tool service
Wraps graph search, node reading, edge queries and other tools for the Report Agent

Core retrieval tools (optimized):
1. InsightForge (deep insight retrieval) - Most powerful hybrid retrieval, auto-generates sub-queries with multi-dimensional search
2. PanoramaSearch (breadth search) - Get full picture, including expired content
3. QuickSearch (simple search) - Quick retrieval
"""

import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.mindgraph_client import MindGraphClient

logger = get_logger('mirofish.graph_tools')


@dataclass
class SearchResult:
    """Search result"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }

    def to_text(self) -> str:
        """Convert to text format for LLM comprehension"""
        text_parts = [f"Search query: {self.query}", f"Found {self.total_count} relevant items"]

        if self.facts:
            text_parts.append("\n### Relevant facts:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")

        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """Node information"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }

    def to_text(self) -> str:
        """Convert to text format"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "Unknown type")
        return f"Entity: {self.name} (type: {entity_type})\nSummary: {self.summary}"


@dataclass
class EdgeInfo:
    """Edge information"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # Temporal information
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }

    def to_text(self, include_temporal: bool = False) -> str:
        """Convert to text format"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"Relationship: {source} --[{self.name}]--> {target}\nFact: {self.fact}"

        if include_temporal:
            valid_at = self.valid_at or "Unknown"
            invalid_at = self.invalid_at or "Present"
            base_text += f"\nValidity: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (expired: {self.expired_at})"

        return base_text

    @property
    def is_expired(self) -> bool:
        """Whether expired"""
        return self.expired_at is not None

    @property
    def is_invalid(self) -> bool:
        """Whether invalidated"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    Deep insight retrieval result (InsightForge)
    Contains retrieval results for multiple sub-queries and comprehensive analysis
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]

    # Multi-dimensional retrieval results
    semantic_facts: List[str] = field(default_factory=list)  # Semantic search results
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # Entity insights
    relationship_chains: List[str] = field(default_factory=list)  # Relationship chains

    # Statistics
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }

    def to_text(self) -> str:
        """Convert to detailed text format for LLM comprehension"""
        text_parts = [
            f"## Deep Predictive Analysis",
            f"Analysis question: {self.query}",
            f"Prediction scenario: {self.simulation_requirement}",
            f"\n### Prediction Data Statistics",
            f"- Relevant prediction facts: {self.total_facts}",
            f"- Entities involved: {self.total_entities}",
            f"- Relationship chains: {self.total_relationships}"
        ]

        # Sub-queries
        if self.sub_queries:
            text_parts.append(f"\n### Analysis Sub-queries")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")

        # Semantic search results
        if self.semantic_facts:
            text_parts.append(f"\n### [Key Facts] (please cite these in the report)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")

        # Entity insights
        if self.entity_insights:
            text_parts.append(f"\n### [Core Entities]")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', 'Unknown')}** ({entity.get('type', 'Entity')})")
                if entity.get('summary'):
                    text_parts.append(f"  Summary: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  Related facts: {len(entity.get('related_facts', []))}")

        # Relationship chains
        if self.relationship_chains:
            text_parts.append(f"\n### [Relationship Chains]")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")

        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    Breadth search result (Panorama)
    Contains all related information, including expired content
    """
    query: str

    # All nodes
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # All edges (including expired)
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # Currently active facts
    active_facts: List[str] = field(default_factory=list)
    # Expired/invalidated facts (historical records)
    historical_facts: List[str] = field(default_factory=list)

    # Statistics
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }

    def to_text(self) -> str:
        """Convert to text format (full version, no truncation)"""
        text_parts = [
            f"## Breadth Search Results (Future Panoramic View)",
            f"Query: {self.query}",
            f"\n### Statistics",
            f"- Total nodes: {self.total_nodes}",
            f"- Total edges: {self.total_edges}",
            f"- Currently active facts: {self.active_count}",
            f"- Historical/expired facts: {self.historical_count}"
        ]

        # Currently active facts (full output, no truncation)
        if self.active_facts:
            text_parts.append(f"\n### [Currently Active Facts] (simulation result originals)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")

        # Historical/expired facts (full output, no truncation)
        if self.historical_facts:
            text_parts.append(f"\n### [Historical/Expired Facts] (evolution process records)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")

        # Key entities (full output, no truncation)
        if self.all_nodes:
            text_parts.append(f"\n### [Entities Involved]")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")
                text_parts.append(f"- **{node.name}** ({entity_type})")

        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """Single agent interview result"""
    agent_name: str
    agent_role: str  # Role type (e.g., student, teacher, media, etc.)
    agent_bio: str  # Bio
    question: str  # Interview question
    response: str  # Interview response
    key_quotes: List[str] = field(default_factory=list)  # Key quotes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }

    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # Display full agent_bio, no truncation
        text += f"_Bio: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**Key Quotes:**\n"
            for quote in self.key_quotes:
                # Clean various quotation marks
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # Remove leading punctuation
                while clean_quote and clean_quote[0] in ',;:.\n\r\t !?':
                    clean_quote = clean_quote[1:]
                # Filter junk content containing question numbers
                skip = False
                for d in '123456789':
                    if f'Question{d}' in clean_quote or f'Question {d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # Truncate overly long content (at period boundaries, not hard truncation)
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('.', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """
    Interview result (Interview)
    Contains interview responses from multiple simulation agents
    """
    interview_topic: str  # Interview topic
    interview_questions: List[str]  # Interview question list

    # Agents selected for interview
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # Interview responses from each agent
    interviews: List[AgentInterview] = field(default_factory=list)

    # Reasoning for agent selection
    selection_reasoning: str = ""
    # Consolidated interview summary
    summary: str = ""

    # Statistics
    total_agents: int = 0
    interviewed_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }

    def to_text(self) -> str:
        """Convert to detailed text format for LLM comprehension and report citation"""
        text_parts = [
            "## In-Depth Interview Report",
            f"**Interview topic:** {self.interview_topic}",
            f"**Interviewees:** {self.interviewed_count} / {self.total_agents} simulation agents",
            "\n### Interviewee Selection Reasoning",
            self.selection_reasoning or "(Auto-selected)",
            "\n---",
            "\n### Interview Transcripts",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### Interview #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("(No interview records)\n\n---")

        text_parts.append("\n### Interview Summary and Key Insights")
        text_parts.append(self.summary or "(No summary)")

        return "\n".join(text_parts)


class GraphToolsService:
    """
    MindGraph retrieval tool service

    [Core retrieval tools - optimized]
    1. insight_forge - Deep insight retrieval (most powerful, auto-generates sub-queries, multi-dimensional search)
    2. panorama_search - Breadth search (get full picture, including expired content)
    3. quick_search - Simple search (quick retrieval)
    4. interview_agents - In-depth interviews (interview simulation agents, get multi-perspective views)

    [Basic tools]
    - search_graph - Graph semantic search
    - get_all_nodes - Get all graph nodes
    - get_all_edges - Get all graph edges (with temporal info)
    - get_node_detail - Get node details
    - get_node_edges - Get edges related to a node
    - get_entities_by_type - Get entities by type
    - get_entity_summary - Get entity relationship summary
    """

    # Class-level cache for full-graph fetches (shared across instances)
    _node_cache: Dict[str, tuple] = {}  # (graph_id, scope) → (nodes, timestamp)
    _edge_cache: Dict[str, tuple] = {}  # (graph_id, scope) → (edges, timestamp)
    _CACHE_TTL = 300  # 5 minutes

    def __init__(self, llm_client: Optional[LLMClient] = None, source: str = "upload"):
        self.client = MindGraphClient()
        self.source = source
        # LLM client for InsightForge sub-query generation
        self._llm_client = llm_client
        logger.info(f"GraphToolsService initialized (source={source})")

    @property
    def llm(self) -> LLMClient:
        """Lazily initialize LLM client"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client

    def search_graph(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Graph semantic search

        Uses hybrid search (semantic + BM25) to search for related information in the graph.
        Falls back to local keyword matching if MindGraph search API is unavailable.

        Args:
            graph_id: Graph ID (project ID)
            query: Search query
            limit: Number of results to return
            scope: Search scope, "edges" or "nodes" (parameter kept for caller compatibility)

        Returns:
            SearchResult: Search results
        """
        logger.info(f"Graph search: graph_id={graph_id}, query={query[:50]}...")

        # Use MindGraph hybrid search API
        # Connect mode: use retrieve_context (unscoped) for broader results
        # Upload mode: use search_hybrid (agent_id scoped)
        try:
            if self.source == "mindgraph":
                search_response = self.client.retrieve_context(
                    query=query, project_id=None, k=limit, depth=1,
                    include_chunks=False,
                )
                # Normalize retrieve_context response to search_hybrid format
                graph_data = search_response.get("graph", {})
                raw_nodes = graph_data.get("nodes", [])
                raw_edges = graph_data.get("edges", [])
                results_list = []
                for n in raw_nodes:
                    results_list.append(n)
                for e in raw_edges:
                    results_list.append(e)
                # Also include top-level results if present
                results_list.extend(search_response.get("results", []))
                search_response = {"results": results_list}
            else:
                search_response = self.client.search_hybrid(
                    query=query,
                    project_id=graph_id,
                    limit=limit
                )

            facts = []
            edges = []
            nodes = []

            results_list = search_response.get("results", [])

            for item in results_list:
                uid = item.get("uid", "")
                label = item.get("label", "")
                summary = item.get("summary", "")
                content = item.get("content", "")
                node_type = item.get("node_type", "")

                # Build facts from content/label
                fact_text = content or summary or label
                if fact_text:
                    facts.append(fact_text)

                # Determine if item is more like a node or edge based on available fields
                from_uid = item.get("from_uid", "")
                to_uid = item.get("to_uid", "")
                edge_type = item.get("edge_type", "")

                if from_uid and to_uid:
                    # This result item looks like an edge
                    edges.append({
                        "uuid": uid,
                        "name": edge_type or label,
                        "fact": fact_text,
                        "source_node_uuid": from_uid,
                        "target_node_uuid": to_uid,
                    })
                else:
                    # This result item looks like a node
                    nodes.append({
                        "uuid": uid,
                        "name": label,
                        "labels": [node_type] if node_type else [],
                        "summary": summary or content,
                    })

            logger.info(f"Search complete: found {len(facts)} relevant facts")

            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )

        except Exception as e:
            logger.warning(f"MindGraph Search API failed, falling back to local search: {str(e)}")
            # Fallback: use local keyword matching search
            return self._local_search(graph_id, query, limit, scope)

    def search_simulation_data(
        self,
        graph_id: str,
        query: str,
        limit: int = 15
    ) -> SearchResult:
        """
        Search only simulation-created nodes (Journal, Decision, Observation, etc.)

        Uses agent_id-scoped search which returns only nodes created by
        the simulation (not the original book/document knowledge).

        Args:
            graph_id: Graph ID (= project_id = agent_id namespace)
            query: Search query
            limit: Max results

        Returns:
            SearchResult with only simulation data
        """
        logger.info(f"Simulation data search: graph_id={graph_id}, query={query[:50]}...")

        try:
            # search_hybrid with project_id filters by agent_id → simulation-only
            search_response = self.client.search_hybrid(
                query=query,
                project_id=graph_id,
                limit=limit
            )

            facts = []
            edges = []
            nodes = []

            for item in search_response.get("results", []):
                label = item.get("label", "")
                summary = item.get("summary", "")
                content = item.get("content", "")
                node_type = item.get("node_type", "")
                uid = item.get("uid", "")

                fact_text = content or summary or label
                if fact_text:
                    # Tag simulation data for clarity
                    type_tag = f"[{node_type}] " if node_type else ""
                    facts.append(f"{type_tag}{fact_text}")

                from_uid = item.get("from_uid", "")
                to_uid = item.get("to_uid", "")
                if from_uid and to_uid:
                    edges.append({
                        "uuid": uid,
                        "name": item.get("edge_type", label),
                        "fact": fact_text,
                        "source_node_uuid": from_uid,
                        "target_node_uuid": to_uid,
                    })
                else:
                    nodes.append({
                        "uuid": uid,
                        "name": label,
                        "labels": [node_type] if node_type else [],
                        "summary": summary or content,
                    })

            logger.info(f"Simulation data search complete: {len(facts)} facts")
            return SearchResult(
                facts=facts, edges=edges, nodes=nodes,
                query=query, total_count=len(facts)
            )
        except Exception as e:
            logger.warning(f"Simulation data search failed: {e}")
            return SearchResult(facts=[], edges=[], nodes=[], query=query, total_count=0)

    def _local_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Local keyword matching search (fallback for MindGraph Search API)

        Gets all edges/nodes, then performs local keyword matching

        Args:
            graph_id: Graph ID
            query: Search query
            limit: Number of results to return
            scope: Search scope

        Returns:
            SearchResult: Search results
        """
        logger.info(f"Using local search: query={query[:30]}...")

        facts = []
        edges_result = []
        nodes_result = []

        # Extract query keywords (simple tokenization)
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]

        def match_score(text: str) -> int:
            """Calculate text-to-query match score"""
            if not text:
                return 0
            text_lower = text.lower()
            # Exact query match
            if query_lower in text_lower:
                return 100
            # Keyword matching
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score

        try:
            if scope in ["edges", "both"]:
                # Get all edges and match
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))

                # Sort by score
                scored_edges.sort(key=lambda x: x[0], reverse=True)

                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })

            if scope in ["nodes", "both"]:
                # Get all nodes and match
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))

                scored_nodes.sort(key=lambda x: x[0], reverse=True)

                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")

            logger.info(f"Local search complete: found {len(facts)} relevant facts")

        except Exception as e:
            logger.error(f"Local search failed: {str(e)}")

        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )

    def get_all_nodes(self, graph_id: str, scope: str = "full") -> List[NodeInfo]:
        """
        Get all nodes from the graph (with caching)

        Args:
            graph_id: Graph ID (project ID)
            scope: "full" = full graph, "simulation" = only simulation-created nodes

        Returns:
            Node list
        """
        import time as _time
        cache_key = f"{graph_id}:{scope}:{self.source}"
        cached = self._node_cache.get(cache_key)
        if cached:
            nodes, ts = cached
            if _time.time() - ts < self._CACHE_TTL:
                logger.info(f"Node cache hit: {len(nodes)} nodes (key={cache_key})")
                return nodes

        logger.info(f"Getting all nodes from graph {graph_id} (source={self.source}, scope={scope})...")

        if scope == "simulation":
            # Only simulation-created nodes (agent_id-scoped)
            mg_nodes = self.client.list_all_nodes(project_id=graph_id)
        elif self.source == "mindgraph":
            mg_nodes = self.client.list_all_graph_nodes()
        else:
            mg_nodes = self.client.list_all_nodes(project_id=graph_id)

        result = []
        for mg_node in mg_nodes:
            node_uuid = mg_node.get("uid", "")
            node_name = mg_node.get("label", "")
            node_type = mg_node.get("node_type", mg_node.get("type", ""))
            node_summary = mg_node.get("summary", "")
            node_props = mg_node.get("props", {})

            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node_name,
                labels=[node_type] if node_type else [],
                summary=node_summary,
                attributes=node_props if isinstance(node_props, dict) else {}
            ))

        self._node_cache[cache_key] = (result, _time.time())
        logger.info(f"Retrieved {len(result)} nodes (cached)")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True,
                      _raw_nodes: list = None) -> List[EdgeInfo]:
        """
        Get all edges from the graph (auto-paginated)

        Args:
            graph_id: Graph ID (project ID)
            include_temporal: Whether to include temporal info (parameter kept for caller compatibility; MindGraph edges don't have time fields)
            _raw_nodes: Pre-fetched raw MindGraph nodes (avoids duplicate queries in connect mode)

        Returns:
            Edge list
        """
        logger.info(f"Getting all edges from graph {graph_id} (source={self.source})...")

        if self.source == "mindgraph":
            mg_edges = self.client.list_all_graph_edges(nodes=_raw_nodes)
        else:
            mg_edges = self.client.list_all_edges(project_id=graph_id)

        result = []
        for mg_edge in mg_edges:
            edge_uuid = mg_edge.get("uid", "")
            edge_name = mg_edge.get("edge_type", "")
            edge_fact = mg_edge.get("label", "")
            source_uid = mg_edge.get("from_uid", "")
            target_uid = mg_edge.get("to_uid", "")

            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge_name,
                fact=edge_fact,
                source_node_uuid=source_uid,
                target_node_uuid=target_uid
            )

            # MindGraph edges don't have expired_at/invalid_at time fields
            # Keep these fields as None (default values)

            result.append(edge_info)

        logger.info(f"Retrieved {len(result)} edges")
        return result

    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        Get detailed information for a single node

        Args:
            node_uuid: Node UID

        Returns:
            Node info or None
        """
        logger.info(f"Getting node details: {node_uuid[:8]}...")

        try:
            mg_node = self.client.get_node(uid=node_uuid)

            if not mg_node:
                return None

            node_type = mg_node.get("node_type", mg_node.get("type", ""))

            return NodeInfo(
                uuid=mg_node.get("uid", ""),
                name=mg_node.get("label", ""),
                labels=[node_type] if node_type else [],
                summary=mg_node.get("summary", ""),
                attributes=mg_node.get("props", {}) if isinstance(mg_node.get("props"), dict) else {}
            )
        except Exception as e:
            logger.error(f"Failed to get node details: {str(e)}")
            return None

    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        Get all edges related to a node

        Uses MindGraph's neighborhood API to get 1-hop neighbors and extract edge info

        Args:
            graph_id: Graph ID
            node_uuid: Node UID

        Returns:
            Edge list
        """
        logger.info(f"Getting edges for node {node_uuid[:8]}...")

        try:
            neighborhood = self.client.get_neighborhood(uid=node_uuid, depth=1)

            result = []
            # Extract edges from neighborhood result
            edges_data = neighborhood.get("edges", [])
            if isinstance(edges_data, list):
                for mg_edge in edges_data:
                    edge_uuid = mg_edge.get("uid", "")
                    edge_info = EdgeInfo(
                        uuid=str(edge_uuid) if edge_uuid else "",
                        name=mg_edge.get("edge_type", ""),
                        fact=mg_edge.get("label", ""),
                        source_node_uuid=mg_edge.get("from_uid", ""),
                        target_node_uuid=mg_edge.get("to_uid", "")
                    )
                    result.append(edge_info)

            logger.info(f"Found {len(result)} edges related to node")
            return result

        except Exception as e:
            logger.warning(f"Failed to get node edges: {str(e)}")
            return []

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str
    ) -> List[NodeInfo]:
        """
        Get entities by type

        Args:
            graph_id: Graph ID (project ID)
            entity_type: Entity type (e.g., Student, PublicFigure, etc.)

        Returns:
            List of entities matching the type
        """
        logger.info(f"Getting entities of type {entity_type}...")

        mg_nodes = self.client.list_all_nodes(project_id=graph_id, node_type=entity_type)

        result = []
        for mg_node in mg_nodes:
            node_uuid = mg_node.get("uid", "")
            node_name = mg_node.get("label", "")
            node_type = mg_node.get("node_type", mg_node.get("type", ""))
            node_summary = mg_node.get("summary", "")
            node_props = mg_node.get("props", {})

            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node_name,
                labels=[node_type] if node_type else [],
                summary=node_summary,
                attributes=node_props if isinstance(node_props, dict) else {}
            ))

        logger.info(f"Found {len(result)} entities of type {entity_type}")
        return result

    def get_entity_summary(
        self,
        graph_id: str,
        entity_name: str
    ) -> Dict[str, Any]:
        """
        Get relationship summary for a specified entity

        Searches for all information related to the entity and generates a summary

        Args:
            graph_id: Graph ID
            entity_name: Entity name

        Returns:
            Entity summary information
        """
        logger.info(f"Getting relationship summary for entity {entity_name}...")

        # First search for information related to this entity
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )

        # Get all edges to find associations
        all_edges = self.get_all_edges(graph_id)

        # Try to find the entity node in search results
        entity_node = None
        # First look in the search result nodes
        for node_data in search_result.nodes:
            if isinstance(node_data, dict) and node_data.get("name", "").lower() == entity_name.lower():
                entity_node = NodeInfo(
                    uuid=node_data.get("uuid", ""),
                    name=node_data.get("name", ""),
                    labels=node_data.get("labels", []),
                    summary=node_data.get("summary", ""),
                    attributes={}
                )
                break

        # If not found in search, iterate all nodes
        if not entity_node:
            all_nodes = self.get_all_nodes(graph_id)
            for node in all_nodes:
                if node.name.lower() == entity_name.lower():
                    entity_node = node
                    break

        related_edges = []
        if entity_node:
            # Use neighborhood API to get associated edges
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)

        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }

    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        Get graph statistics

        Args:
            graph_id: Graph ID (project ID)

        Returns:
            Statistics
        """
        logger.info(f"Getting statistics for graph {graph_id}...")

        try:
            # Try using the MindGraph client's built-in statistics method
            mg_stats = self.client.get_graph_statistics(project_id=graph_id)

            # Normalize into the expected format
            entity_types = mg_stats.get("type_distribution", {})

            return {
                "graph_id": graph_id,
                "total_nodes": mg_stats.get("node_count", 0),
                "total_edges": mg_stats.get("edge_count", 0),
                "entity_types": entity_types,
                "relation_types": {}  # MindGraph statistics don't break down relation types separately
            }
        except Exception as e:
            logger.warning(f"MindGraph get_graph_statistics failed, falling back to manual statistics: {str(e)}")

        # Fallback: manually compute statistics
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)

        # Count entity type distribution
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1

        # Count relationship type distribution
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1

        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }

    def get_simulation_context(
        self,
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        Get simulation-related context information

        Comprehensive search for all information related to simulation requirements

        Args:
            graph_id: Graph ID
            simulation_requirement: Simulation requirement description
            limit: Quantity limit per category

        Returns:
            Simulation context information
        """
        logger.info(f"Getting simulation context: {simulation_requirement[:50]}...")

        # Search for information related to simulation requirements
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )

        # Get graph statistics
        stats = self.get_graph_statistics(graph_id)

        # Get all entity nodes
        all_nodes = self.get_all_nodes(graph_id)

        # Filter entities with actual types (non-pure Entity nodes)
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })

        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # Limit quantity
            "total_entities": len(entities)
        }

    # ========== Core retrieval tools (optimized) ==========

    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        [InsightForge - Deep Insight Retrieval]

        Most powerful hybrid retrieval function, auto-decomposes questions with multi-dimensional search:
        1. Use LLM to decompose the question into multiple sub-queries
        2. Perform semantic search for each sub-query
        3. Extract related entities and get their detailed info
        4. Trace relationship chains
        5. Integrate all results to generate deep insights

        Args:
            graph_id: Graph ID
            query: User question
            simulation_requirement: Simulation requirement description
            report_context: Report context (optional, for more precise sub-query generation)
            max_sub_queries: Maximum number of sub-queries

        Returns:
            InsightForgeResult: Deep insight retrieval result
        """
        logger.info(f"InsightForge deep insight retrieval: {query[:50]}...")

        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )

        # Step 1: Use LLM to generate sub-queries
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"Generated {len(sub_queries)} sub-queries")

        # Step 2: Perform semantic search for each sub-query
        all_facts = []
        all_edges = []
        seen_facts = set()

        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )

            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)

            all_edges.extend(search_result.edges)

        # Also search the original question
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)

        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)

        # Step 3: Extract related entity UUIDs from edges, get info for these entities only (not all nodes)
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)

        # Batch-fetch details for all related entities (single API call)
        entity_insights = []
        node_map = {}  # For subsequent relationship chain construction

        valid_uuids = [u for u in entity_uuids if u]
        if valid_uuids:
            try:
                mg_nodes = self.client.get_nodes_batch(valid_uuids)
                for mg_node in mg_nodes:
                    node_uuid = mg_node.get("uid", "")
                    node_type = mg_node.get("node_type", mg_node.get("type", ""))
                    node = NodeInfo(
                        uuid=node_uuid,
                        name=mg_node.get("label", ""),
                        labels=[node_type] if node_type else [],
                        summary=mg_node.get("summary", ""),
                        attributes=mg_node.get("props", {}) if isinstance(mg_node.get("props"), dict) else {}
                    )
                    node_map[node_uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")

                    related_facts = [
                        f for f in all_facts
                        if node.name.lower() in f.lower()
                    ]

                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts
                    })
                logger.info(f"Batch-fetched {len(mg_nodes)} entity details")
            except Exception as e:
                logger.warning(f"Batch node fetch failed, falling back to individual fetch: {e}")
                for uuid in valid_uuids:
                    try:
                        node = self.get_node_detail(uuid)
                        if node:
                            node_map[uuid] = node
                            entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")
                            related_facts = [f for f in all_facts if node.name.lower() in f.lower()]
                            entity_insights.append({
                                "uuid": node.uuid, "name": node.name,
                                "type": entity_type, "summary": node.summary,
                                "related_facts": related_facts
                            })
                    except Exception:
                        continue

        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)

        # Step 4: Build all relationship chains (no limit)
        relationship_chains = []
        for edge_data in all_edges:  # Process all edges, no truncation
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')

                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]

                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)

        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)

        logger.info(f"InsightForge complete: {result.total_facts} facts, {result.total_entities} entities, {result.total_relationships} relationships")
        return result

    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        Use LLM to generate sub-queries

        Decompose a complex question into multiple independently searchable sub-queries
        """
        system_prompt = """You are a professional question analysis expert. Your task is to decompose a complex question into multiple sub-questions that can be independently observed in a simulated world.

Requirements:
1. Each sub-question should be specific enough to find relevant agent behaviors or events in the simulated world
2. Sub-questions should cover different dimensions of the original question (e.g., who, what, why, how, when, where)
3. Sub-questions should be relevant to the simulation scenario
4. Return in JSON format: {"sub_queries": ["sub-question 1", "sub-question 2", ...]}"""

        user_prompt = f"""Simulation requirement background:
{simulation_requirement}

{f"Report context: {report_context[:500]}" if report_context else ""}

Please decompose the following question into {max_queries} sub-questions:
{query}

Return a JSON-formatted list of sub-questions."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )

            sub_queries = response.get("sub_queries", [])
            # Ensure it's a list of strings
            return [str(sq) for sq in sub_queries[:max_queries]]

        except Exception as e:
            logger.warning(f"Failed to generate sub-queries: {str(e)}, using default sub-queries")
            # Fallback: return variants based on the original question
            return [
                query,
                f"Key participants in {query}",
                f"Causes and impacts of {query}",
                f"Development process of {query}"
            ][:max_queries]

    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        [PanoramaSearch - Breadth Search]

        Get a full-picture view, including all related content and historical/expired information:
        1. Get all related nodes
        2. Get all edges
        3. Categorize and organize currently active information

        Note: MindGraph does not include expired_at/invalid_at time fields,
        so all edges are treated as currently active (historical_facts is always empty).

        This tool is suitable for scenarios requiring a full event overview and tracking evolution.

        Args:
            graph_id: Graph ID
            query: Search query (for relevance sorting)
            include_expired: Whether to include expired content (parameter kept for caller compatibility; MindGraph has no expiry concept)
            limit: Result quantity limit

        Returns:
            PanoramaResult: Breadth search result
        """
        logger.info(f"PanoramaSearch breadth search: {query[:50]}...")

        result = PanoramaResult(query=query)

        # Get all nodes
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)

        # Get all edges
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)

        # Categorize facts
        # MindGraph doesn't have expired_at/invalid_at fields -- all edges treated as currently active
        active_facts = []
        historical_facts = []  # Always empty

        for edge in all_edges:
            if not edge.fact:
                continue

            # All MindGraph edges treated as currently active
            active_facts.append(edge.fact)

        # Relevance sorting based on query
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]

        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score

        # Sort and limit quantity
        active_facts.sort(key=relevance_score, reverse=True)

        result.active_facts = active_facts[:limit]
        result.active_count = len(active_facts)

        # Reconstruct temporal evolution via version history (MindGraph has no expired_at/invalid_at)
        historical_facts = []
        if all_nodes:
            sample_nodes = all_nodes[:5]  # Sample the first 5 most relevant entities
            for node in sample_nodes:
                try:
                    history = self.client.get_node_history(node.uuid)
                    if len(history) > 1:
                        for old_version in history[:-1]:
                            old_label = old_version.get("label", "")
                            old_confidence = old_version.get("confidence")
                            if old_label and old_label != node.name:
                                entry = f"[Historical] {node.name} was previously: {old_label}"
                                if old_confidence is not None:
                                    entry += f" (confidence: {old_confidence})"
                                historical_facts.append(entry)
                except Exception:
                    pass

        result.historical_facts = historical_facts
        result.historical_count = len(historical_facts)

        logger.info(f"PanoramaSearch complete: {result.active_count} active, {result.historical_count} historical")
        return result

    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        [QuickSearch - Simple Search]

        Fast, lightweight retrieval tool:
        1. Directly calls MindGraph hybrid search
        2. Returns the most relevant results
        3. Suitable for simple, direct retrieval needs

        Args:
            graph_id: Graph ID
            query: Search query
            limit: Number of results to return

        Returns:
            SearchResult: Search results
        """
        logger.info(f"QuickSearch simple search: {query[:50]}...")

        # Directly call the existing search_graph method
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )

        logger.info(f"QuickSearch complete: {result.total_count} results")
        return result

    # ========== Cognitive query tools (MindGraph-specific) ==========

    def get_weak_claims(self, graph_id: str, limit: int = 20) -> SearchResult:
        """Get low-confidence claims -- the most uncertain predictions"""
        logger.info(f"Getting weak claims: graph_id={graph_id}, limit={limit}")
        try:
            result = self.client.get_weak_claims(project_id=graph_id, limit=limit)
            facts = []
            nodes = []
            for item in result.get("results", result.get("items", [])):
                content = item.get("content", "") or item.get("label", "")
                confidence = item.get("confidence", "N/A")
                if content:
                    facts.append(f"[confidence={confidence}] {content}")
                nodes.append(item)
            return SearchResult(facts=facts, edges=[], nodes=nodes, query="weak_claims", total_count=len(facts))
        except Exception as e:
            logger.error(f"Failed to get weak claims: {e}")
            return SearchResult(facts=[], edges=[], nodes=[], query="weak_claims", total_count=0)

    def get_contradictions(self, graph_id: str, limit: int = 20) -> SearchResult:
        """Get unresolved contradictions -- conflicting viewpoints between agents"""
        logger.info(f"Getting contradictions: graph_id={graph_id}, limit={limit}")
        try:
            result = self.client.get_contradictions(project_id=graph_id, limit=limit)
            facts = []
            edges = []
            for item in result.get("results", result.get("items", [])):
                content = item.get("content", "") or item.get("label", "")
                if content:
                    facts.append(f"[Contradiction] {content}")
                edges.append(item)
            return SearchResult(facts=facts, edges=edges, nodes=[], query="contradictions", total_count=len(facts))
        except Exception as e:
            logger.error(f"Failed to get contradictions: {e}")
            return SearchResult(facts=[], edges=[], nodes=[], query="contradictions", total_count=0)

    def get_open_questions(self, graph_id: str, limit: int = 20) -> SearchResult:
        """Get open questions -- unanswered questions in the simulation"""
        logger.info(f"Getting open questions: graph_id={graph_id}, limit={limit}")
        try:
            result = self.client.get_open_questions(project_id=graph_id, limit=limit)
            facts = []
            nodes = []
            for item in result.get("results", result.get("items", [])):
                content = item.get("content", "") or item.get("text", "") or item.get("label", "")
                if content:
                    facts.append(f"[Question] {content}")
                nodes.append(item)
            return SearchResult(facts=facts, edges=[], nodes=nodes, query="open_questions", total_count=len(facts))
        except Exception as e:
            logger.error(f"Failed to get open questions: {e}")
            return SearchResult(facts=[], edges=[], nodes=[], query="open_questions", total_count=0)

    # ========== Graph traversal tools (MindGraph-specific) ==========

    def trace_reasoning_chain(self, graph_id: str, entity_name: str, max_depth: int = 3) -> str:
        """Trace reasoning chain starting from a specified entity"""
        logger.info(f"Tracing reasoning chain: entity={entity_name}, depth={max_depth}")
        search = self.search_graph(graph_id, entity_name, limit=1)
        if not search.nodes:
            return f"Entity not found: {entity_name}"

        uid = search.nodes[0].get("uuid", "")
        if not uid:
            return f"Entity UID not found: {entity_name}"

        try:
            chain = self.client.traverse_chain(uid, max_depth=max_depth)
            steps = chain.get("steps", [])
            text_parts = [f"## Reasoning Chain (from {entity_name}, depth={max_depth})"]
            for i, step in enumerate(steps):
                edge_type = step.get("edge_type", "")
                label = step.get("label", "")
                depth_val = step.get("depth", i + 1)
                text_parts.append(f"{i+1}. [depth {depth_val}] --{edge_type}--> {label}")
            return "\n".join(text_parts) if len(text_parts) > 1 else "No reasoning chain found"
        except Exception as e:
            logger.error(f"Failed to trace reasoning chain: {e}")
            return f"Reasoning chain query failed: {str(e)}"

    def get_belief_history(self, graph_id: str, entity_name: str) -> str:
        """Get entity evolution history (version history -> belief evolution)"""
        logger.info(f"Getting belief history: entity={entity_name}")
        search = self.search_graph(graph_id, entity_name, limit=1)
        if not search.nodes:
            return f"Entity not found: {entity_name}"

        uid = search.nodes[0].get("uuid", "")
        if not uid:
            return f"Entity UID not found: {entity_name}"

        try:
            history = self.client.get_node_history(uid)
            text_parts = [f"## Evolution History of {entity_name} ({len(history)} versions)"]
            for v in history:
                version = v.get("version", 0)
                label = v.get("label", "")
                confidence = v.get("confidence", "N/A")
                changed_by = v.get("changed_by", "")
                text_parts.append(
                    f"- v{version}: {label} (confidence={confidence}, changed_by={changed_by})"
                )
            return "\n".join(text_parts) if len(text_parts) > 1 else "No history records"
        except Exception as e:
            logger.error(f"Failed to get belief history: {e}")
            return f"History query failed: {str(e)}"

    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        [InterviewAgents - In-Depth Interview]

        Calls the real OASIS interview API to interview running agents in the simulation:
        1. Auto-read profile files to learn about all simulation agents
        2. Use LLM to analyze interview requirements and intelligently select the most relevant agents
        3. Use LLM to generate interview questions
        4. Call /api/simulation/interview/batch endpoint for real interviews (dual-platform simultaneous)
        5. Integrate all interview results and generate interview report

        [Important] This feature requires the simulation environment to be running (OASIS env not closed)

        [Use Cases]
        - Need to understand event perspectives from different roles
        - Need to collect opinions and viewpoints from multiple parties
        - Need to get real agent responses (not LLM-simulated)

        Args:
            simulation_id: Simulation ID (for locating profile files and calling interview API)
            interview_requirement: Interview requirement description (unstructured, e.g., "understand student views on the event")
            simulation_requirement: Simulation requirement background (optional)
            max_agents: Maximum number of agents to interview
            custom_questions: Custom interview questions (optional, auto-generated if not provided)

        Returns:
            InterviewResult: Interview result
        """
        from .simulation_runner import SimulationRunner

        logger.info(f"InterviewAgents in-depth interview (real API): {interview_requirement[:50]}...")

        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )

        # Step 1: Read profile files
        profiles = self._load_agent_profiles(simulation_id)

        if not profiles:
            logger.warning(f"No profile files found for simulation {simulation_id}")
            result.summary = "No agent profile files found for interviews"
            return result

        result.total_agents = len(profiles)
        logger.info(f"Loaded {len(profiles)} agent profiles")

        # Step 2: Use LLM to select agents for interview (returns agent_id list)
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )

        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"Selected {len(selected_agents)} agents for interview: {selected_indices}")

        # Step 3: Generate interview questions (if not provided)
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"Generated {len(result.interview_questions)} interview questions")

        # Combine questions into a single interview prompt
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])

        # Add optimized prefix to constrain agent response format
        INTERVIEW_PROMPT_PREFIX = (
            "You are being interviewed. Please answer the following questions based on your persona, "
            "all past memories, and actions, responding directly in plain text.\n"
            "Response requirements:\n"
            "1. Answer directly in natural language, do not call any tools\n"
            "2. Do not return JSON format or tool call format\n"
            "3. Do not use Markdown headings (e.g., #, ##, ###)\n"
            "4. Answer each question in order, starting each answer with 'Question X:' (X is the question number)\n"
            "5. Separate answers to each question with blank lines\n"
            "6. Provide substantive answers, at least 2-3 sentences per question\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"

        # Step 4: Call real interview API (no platform specified, dual-platform by default)
        try:
            # Build batch interview list (no platform specified, dual-platform interview)
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt  # Use optimized prompt
                    # No platform specified, API will interview on both twitter and reddit
                })

            logger.info(f"Calling batch interview API (dual-platform): {len(interviews_request)} agents")

            # Call SimulationRunner's batch interview method (no platform, dual-platform interview)
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # No platform specified, dual-platform interview
                timeout=180.0   # Dual-platform needs longer timeout
            )

            logger.info(f"Interview API returned: {api_result.get('interviews_count', 0)} results, success={api_result.get('success')}")

            # Check if API call was successful
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "Unknown error")
                logger.warning(f"Interview API returned failure: {error_msg}")
                result.summary = f"Interview API call failed: {error_msg}. Please check OASIS simulation environment status."
                return result

            # Step 5: Parse API return results, build AgentInterview objects
            # Dual-platform mode return format: {"twitter_0": {...}, "reddit_0": {...}, "twitter_1": {...}, ...}
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}

            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "Unknown")
                agent_bio = agent.get("bio", "")

                # Get interview results for this agent on both platforms
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})

                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # Clean possible tool call JSON wrappers
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # Always output dual-platform markers
                twitter_text = twitter_response if twitter_response else "(No response from this platform)"
                reddit_text = reddit_response if reddit_response else "(No response from this platform)"
                response_text = f"[Twitter Platform Response]\n{twitter_text}\n\n[Reddit Platform Response]\n{reddit_text}"

                # Extract key quotes (from both platform responses)
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # Clean response text: remove markers, numbering, Markdown and other noise
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'Question\s*\d+[：:]\s*', '', clean_text)
                clean_text = re.sub(r'\[[^\]]+\]', '', clean_text)

                # Strategy 1 (primary): Extract complete sentences with substantive content
                sentences = re.split(r'[.!?]', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W,;:]+', s.strip())
                    and not s.strip().startswith(('{', 'Question'))
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s + "." for s in meaningful[:3]]

                # Strategy 2 (supplementary): Long text within properly paired quotes
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'"([^"]{15,100})"', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[,;:]', q)][:3]

                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],  # Expanded bio length limit
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)

            result.interviewed_count = len(result.interviews)

        except ValueError as e:
            # Simulation environment not running
            logger.warning(f"Interview API call failed (environment not running?): {e}")
            result.summary = f"Interview failed: {str(e)}. Simulation environment may be closed, please ensure OASIS environment is running."
            return result
        except Exception as e:
            logger.error(f"Interview API call exception: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"Error occurred during interview: {str(e)}"
            return result

        # Step 6: Generate interview summary
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )

        logger.info(f"InterviewAgents complete: interviewed {result.interviewed_count} agents (dual-platform)")
        return result

    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """Clean JSON tool call wrappers from agent responses, extract actual content"""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """Load simulation agent profile files"""
        import os
        import csv

        # Build profile file path
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )

        profiles = []

        # Try reading Reddit JSON format first
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"Loaded {len(profiles)} profiles from reddit_profiles.json")
                return profiles
            except Exception as e:
                logger.warning(f"Failed to read reddit_profiles.json: {e}")

        # Try reading Twitter CSV format
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # Convert CSV format to unified format
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "Unknown"
                        })
                logger.info(f"Loaded {len(profiles)} profiles from twitter_profiles.csv")
                return profiles
            except Exception as e:
                logger.warning(f"Failed to read twitter_profiles.csv: {e}")

        return profiles

    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        Use LLM to select agents for interview

        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: Full info list of selected agents
                - selected_indices: Index list of selected agents (for API calls)
                - reasoning: Selection reasoning
        """

        # Build agent summary list
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "Unknown"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)

        system_prompt = """You are a professional interview planning expert. Your task is to select the most suitable interview subjects from the simulation agent list based on interview requirements.

Selection criteria:
1. Agent's identity/profession is relevant to the interview topic
2. Agent may hold unique or valuable perspectives
3. Select diverse viewpoints (e.g., supporters, opponents, neutral parties, professionals, etc.)
4. Prioritize roles directly related to the event

Return in JSON format:
{
    "selected_indices": [list of selected agent indices],
    "reasoning": "explanation of selection reasoning"
}"""

        user_prompt = f"""Interview requirements:
{interview_requirement}

Simulation background:
{simulation_requirement if simulation_requirement else "Not provided"}

Available agent list ({len(agent_summaries)} total):
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

Please select up to {max_agents} agents most suitable for interview, and explain the selection reasoning."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )

            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "Auto-selected based on relevance")

            # Get full info for selected agents
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)

            return selected_agents, valid_indices, reasoning

        except Exception as e:
            logger.warning(f"LLM agent selection failed, using default selection: {e}")
            # Fallback: select the first N agents
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "Using default selection strategy"

    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """Use LLM to generate interview questions"""

        agent_roles = [a.get("profession", "Unknown") for a in selected_agents]

        system_prompt = """You are a professional journalist/interviewer. Generate 3-5 in-depth interview questions based on the interview requirements.

Question requirements:
1. Open-ended questions that encourage detailed responses
2. Questions that different roles might answer differently
3. Cover multiple dimensions including facts, opinions, and feelings
4. Natural language, like a real interview
5. Keep each question under 50 words, concise and clear
6. Ask directly, do not include background explanations or prefixes

Return in JSON format: {"questions": ["question 1", "question 2", ...]}"""

        user_prompt = f"""Interview requirements: {interview_requirement}

Simulation background: {simulation_requirement if simulation_requirement else "Not provided"}

Interviewee roles: {', '.join(agent_roles)}

Please generate 3-5 interview questions."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )

            return response.get("questions", [f"What are your views on {interview_requirement}?"])

        except Exception as e:
            logger.warning(f"Failed to generate interview questions: {e}")
            return [
                f"What is your perspective on {interview_requirement}?",
                "How does this affect you or the group you represent?",
                "How do you think this issue should be resolved or improved?"
            ]

    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """Generate interview summary"""

        if not interviews:
            return "No interviews completed"

        # Collect all interview content
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"[{interview.agent_name} ({interview.agent_role})]\n{interview.response[:500]}")

        system_prompt = """You are a professional news editor. Please generate an interview summary based on multiple interviewees' responses.

Summary requirements:
1. Distill the main viewpoints from each party
2. Identify consensus and disagreements
3. Highlight valuable quotes
4. Be objective and neutral, not favoring any side
5. Keep within 1000 words

Format constraints (must follow):
- Use plain text paragraphs, separate sections with blank lines
- Do not use Markdown headings (e.g., #, ##, ###)
- Do not use dividers (e.g., ---, ***)
- When quoting interviewees, use quotation marks
- You may use **bold** to mark keywords, but do not use other Markdown syntax"""

        user_prompt = f"""Interview topic: {interview_requirement}

Interview content:
{"".join(interview_texts)}

Please generate an interview summary."""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary

        except Exception as e:
            logger.warning(f"Failed to generate interview summary: {e}")
            # Fallback: simple concatenation
            return f"Interviewed {len(interviews)} respondents, including: " + ", ".join([i.agent_name for i in interviews])
