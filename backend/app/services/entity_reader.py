"""
Entity reading and filtering service
Reads nodes from MindGraph graph and filters those matching predefined entity types
"""

import time
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field

from ..config import Config
from ..utils.logger import get_logger
from ..utils.mindgraph_client import MindGraphClient

logger = get_logger('mirofish.entity_reader')

T = TypeVar('T')


@dataclass
class EntityNode:
    """Entity node data structure"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # Related edge information
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # Related node information
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }

    def get_entity_type(self) -> Optional[str]:
        """Get entity type (excluding the default Entity label)"""
        for label in self.labels:
            if label not in ["Entity", "Node", "Unknown"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """Filtered entity collection"""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


class EntityReader:
    """
    Entity reading and filtering service

    Main features:
    1. Read all nodes from MindGraph graph
    2. Filter nodes matching predefined entity types
    3. Get related edges and associated node info for each entity
    """

    def __init__(self):
        if not Config.MINDGRAPH_API_KEY:
            raise ValueError("MINDGRAPH_API_KEY is not configured")
        self.client = MindGraphClient()

    @staticmethod
    def _normalize_node(mg_node: Dict[str, Any]) -> Dict[str, Any]:
        """Convert MindGraph node to internal format"""
        node_type = mg_node.get("node_type", mg_node.get("type", "Entity"))
        return {
            "uuid": mg_node.get("uid", ""),
            "name": mg_node.get("label", mg_node.get("name", "")),
            "labels": [node_type] if node_type else ["Entity"],
            "summary": mg_node.get("summary", "") or mg_node.get("props", {}).get("content", ""),
            "attributes": mg_node.get("props", {}),
        }

    @staticmethod
    def _normalize_edge(mg_edge: Dict[str, Any]) -> Dict[str, Any]:
        """Convert MindGraph edge to internal format"""
        return {
            "uuid": mg_edge.get("uid", ""),
            "name": mg_edge.get("edge_type", mg_edge.get("type", "")),
            "fact": mg_edge.get("label", mg_edge.get("content", "")),
            "source_node_uuid": mg_edge.get("from_uid", mg_edge.get("source_uid", "")),
            "target_node_uuid": mg_edge.get("to_uid", mg_edge.get("target_uid", "")),
            "attributes": mg_edge.get("props", {}),
        }

    def get_all_nodes(self, graph_id: str, source: str = "upload") -> List[Dict[str, Any]]:
        """
        Get all nodes from the graph

        Args:
            graph_id: Graph ID (MindGraph namespace)
            source: Data source - "upload" (filter by agent_id) or "mindgraph" (read full graph)

        Returns:
            Node list (internal format)
        """
        logger.info(f"Getting graph nodes: graph_id={graph_id}, source={source}")
        if source == "mindgraph":
            mg_nodes = self.client.list_all_graph_nodes()
        else:
            mg_nodes = self.client.list_all_nodes(project_id=graph_id)
        nodes_data = [self._normalize_node(n) for n in mg_nodes]
        logger.info(f"Retrieved {len(nodes_data)} nodes")
        return nodes_data

    def get_all_edges(self, graph_id: str, source: str = "upload",
                      raw_nodes: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        Get all edges from the graph

        Args:
            graph_id: Graph ID
            source: Data source
            raw_nodes: Pre-fetched raw MindGraph nodes (to avoid duplicate queries)

        Returns:
            Edge list (internal format)
        """
        logger.info(f"Getting graph edges: graph_id={graph_id}, source={source}")
        if source == "mindgraph":
            mg_edges = self.client.list_all_graph_edges(nodes=raw_nodes)
        else:
            mg_edges = self.client.list_all_edges(project_id=graph_id)
        edges_data = [self._normalize_edge(e) for e in mg_edges]
        logger.info(f"Retrieved {len(edges_data)} edges")
        return edges_data

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """
        Get all related edges for a given node

        Args:
            node_uuid: Node UUID

        Returns:
            Edge list
        """
        try:
            neighborhood = self.client.get_neighborhood(uid=node_uuid, depth=1)
            # Extract edges from neighborhood result
            edges_raw = neighborhood.get("edges", [])
            return [self._normalize_edge(e) for e in edges_raw]
        except Exception as e:
            logger.warning(f"Failed to get edges for node {node_uuid}: {str(e)}")
            return []

    @staticmethod
    def _get_keywords(requirement: str) -> str:
        """Summarize a simulation requirement into English keywords."""
        try:
            from .graph_context_provider import GraphContextProvider
            keywords = GraphContextProvider._summarize_feed(requirement)
            if keywords:
                return keywords
        except Exception as e:
            logger.warning(f"Requirement summarization error: {e}")
        return requirement[:200]

    # Entity types that can act as simulation agents
    _AGENT_TYPE_KEYWORDS = {
        "person", "human", "org", "organization", "organisation",
        "group", "government", "military", "party", "agency",
        "institution", "company", "corporation", "alliance", "coalition",
        "committee", "council", "ministry", "department", "force",
    }

    @classmethod
    def _is_agent_compatible(cls, entity_type_str: str) -> bool:
        if not entity_type_str:
            return False
        lower = entity_type_str.lower()
        return any(kw in lower for kw in cls._AGENT_TYPE_KEYWORDS)

    def _semantic_search_entities(
        self,
        query: str,
        k: int,
        seen_uids: Set[str],
        defined_entity_types: Optional[List[str]] = None,
    ) -> tuple:
        """Run one semantic search pass. Returns (filtered_entities, entity_types_found, skipped_types, new_seen_uids)."""
        try:
            results = self.client.semantic_search(
                query=query,
                k=k,
                node_types=["Entity"],
            )
        except Exception as e:
            logger.warning(f"Semantic search failed for query '{query[:60]}...': {e}")
            return [], set(), {}, seen_uids

        # /retrieve returns [{"node": {...}, "score": ...}, ...] — unwrap
        nodes = [r.get("node", r) if isinstance(r, dict) and "node" in r else r for r in results]
        logger.info(f"Semantic search returned {len(nodes)} Entity nodes for query '{query[:60]}...'")

        filtered = []
        types_found: Set[str] = set()
        skipped: Dict[str, int] = {}

        for mg_node in nodes:
            uid = mg_node.get("uid", "")
            if not uid or uid in seen_uids:
                continue
            seen_uids.add(uid)

            entity_type = mg_node.get("entity_type", "") or mg_node.get("props", {}).get("entity_type", "")

            if defined_entity_types:
                if entity_type not in defined_entity_types:
                    continue
            elif not self._is_agent_compatible(entity_type):
                skipped[entity_type] = skipped.get(entity_type, 0) + 1
                continue

            types_found.add(entity_type)
            node = self._normalize_node(mg_node)
            entity_labels = list(node["labels"])
            if entity_type and entity_type not in entity_labels:
                entity_labels.append(entity_type)

            filtered.append(EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=entity_labels,
                summary=node["summary"],
                attributes=node["attributes"],
            ))

        if skipped:
            logger.info(f"Skipped non-agent types: {dict(sorted(skipped.items(), key=lambda x: -x[1]))}")

        return filtered, types_found, skipped, seen_uids

    _STAKEHOLDER_QUERY_PROMPT = (
        "Given this simulation scenario, list the key EXTERNAL stakeholder "
        "groups who would react to or be affected by this situation. "
        "Focus on parties OUTSIDE the primary subject — allies, adversaries, "
        "regional powers, international organizations, and major world powers.\n\n"
        "Return a single line of comma-separated names/keywords (countries, "
        "organizations, leaders) that are NOT the primary subject but are "
        "key stakeholders. Example format:\n"
        "United States, China, India, European Union, United Nations, NATO, "
        "Saudi Arabia, IMF\n\n"
        "Scenario: {requirement}"
    )

    def _generate_stakeholder_query(self, requirement: str, keywords: str) -> str:
        """Use LLM to generate a stakeholder-focused search query.

        Returns a query targeting external parties (allies, adversaries,
        international orgs) so Pass 2 finds entities DIFFERENT from the
        topic-focused Pass 1.
        """
        try:
            from .graph_context_provider import GraphContextProvider
            client, model = GraphContextProvider._get_llm_client()
            if not client:
                return ""

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": self._STAKEHOLDER_QUERY_PROMPT.format(requirement=requirement)},
                ],
                max_tokens=300,
                temperature=0.3,
            )
            content = (response.choices[0].message.content or "").strip()
            # Strip any <think> tags from reasoning models
            import re
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            if content:
                logger.info(f"Stakeholder query: {content[:120]}")
                return content
        except Exception as e:
            logger.warning(f"Stakeholder query generation failed: {e}")

        return ""

    def _select_entities_by_retrieval(
        self,
        graph_id: str,
        simulation_requirement: str,
        max_entities: int,
        defined_entity_types: Optional[List[str]] = None,
    ) -> Optional['FilteredEntities']:
        """Select relevant entities using MindGraph semantic search.

        Two-pass approach:
        1. Topic query — finds entities semantically close to the scenario
        2. Stakeholder query — uses LLM to identify external parties (allies,
           adversaries, international orgs) and searches for those specifically,
           ensuring entity diversity across all sides of the scenario

        Uses POST /retrieve with action="semantic" and node_types=["Entity"].
        Searches the full org graph (no agent_id scoping) intentionally.

        Returns None if both passes fail, signaling caller to use fallback.
        """
        keywords = self._get_keywords(simulation_requirement)
        logger.info(f"Semantic entity search: query='{keywords[:80]}...', max={max_entities}")

        seen_uids: Set[str] = set()
        all_entities: list = []
        all_types: Set[str] = set()
        search_k = max(200, max_entities * 8)

        # Pass 1: topic-based search (finds entities close to the primary subject)
        entities_1, types_1, _, seen_uids = self._semantic_search_entities(
            query=keywords,
            k=search_k,
            seen_uids=seen_uids,
            defined_entity_types=defined_entity_types,
        )
        all_entities.extend(entities_1)
        all_types.update(types_1)
        logger.info(f"Pass 1 (topic): {len(entities_1)} agent-compatible entities")

        # Pass 2: stakeholder search — query for external parties by name
        if len(all_entities) < max_entities:
            stakeholder_query = self._generate_stakeholder_query(simulation_requirement, keywords)
            if not stakeholder_query:
                # Fallback to static query if LLM call fails
                stakeholder_query = (
                    f"key leaders officials organizations governments from all sides "
                    f"— allies, adversaries, mediators, international observers — "
                    f"involved in {keywords}"
                )
            entities_2, types_2, _, seen_uids = self._semantic_search_entities(
                query=stakeholder_query,
                k=search_k,
                seen_uids=seen_uids,
                defined_entity_types=defined_entity_types,
            )
            all_entities.extend(entities_2)
            all_types.update(types_2)
            logger.info(f"Pass 2 (stakeholders): {len(entities_2)} additional agent-compatible entities")

        if not all_entities:
            logger.warning("Semantic search found 0 agent-compatible entities across both passes")
            return None

        # Dedup by name, keep first (highest similarity from earlier pass)
        best_by_name: Dict[str, 'EntityNode'] = {}
        for entity in all_entities:
            if entity.name not in best_by_name:
                best_by_name[entity.name] = entity
        deduped = list(best_by_name.values())

        selected = deduped[:max_entities]

        # Enrich selected entities with edges
        if selected:
            try:
                edges = self.client.get_edges_batch([e.uuid for e in selected])
                for edge in edges:
                    norm_edge = self._normalize_edge(edge)
                    src, tgt = norm_edge["source_node_uuid"], norm_edge["target_node_uuid"]
                    for entity in selected:
                        if entity.uuid == src:
                            entity.related_edges.append({
                                "direction": "outgoing",
                                "edge_name": norm_edge["name"],
                                "fact": norm_edge["fact"],
                                "target_node_uuid": tgt,
                            })
                        elif entity.uuid == tgt:
                            entity.related_edges.append({
                                "direction": "incoming",
                                "edge_name": norm_edge["name"],
                                "fact": norm_edge["fact"],
                                "source_node_uuid": src,
                            })
            except Exception as e:
                logger.warning(f"Edge enrichment failed: {e}")

        logger.info(
            f"Semantic selection complete: {len(all_entities)} agent-compatible → "
            f"{len(deduped)} deduped → {len(selected)} selected"
        )
        if selected:
            logger.info(f"  Top entities: {[e.name for e in selected[:10]]}")

        return FilteredEntities(
            entities=selected,
            entity_types=all_types,
            total_count=len(all_entities),
            filtered_count=len(selected),
        )

    def _rank_by_relevance(
        self,
        entities: List['EntityNode'],
        requirement: str,
        max_entities: int,
    ) -> List['EntityNode']:
        """Rank entities by relevance to the simulation requirement.

        1. Summarize requirement into English keywords
        2. retrieve_context(keywords, k=20, layer='epistemic') → chunks
        3. Count entity name mentions in chunk text
        4. Rank by mention count, break ties with edge count
        5. Pad with highest-edge-count entities if needed
        """
        from collections import Counter

        entity_names = {e.name for e in entities if len(e.name) >= 3}
        total = len(entities)

        keywords = self._get_keywords(requirement)
        logger.info(f"Entity relevance ranking: query='{keywords[:80]}...'")

        # Step 2: Retrieve epistemic context
        try:
            from mindgraph import MindGraph
            import os
            mg = MindGraph(
                os.environ.get("MINDGRAPH_BASE_URL", "https://api.mindgraph.cloud"),
                api_key=os.environ.get("MINDGRAPH_API_KEY", ""),
                timeout=300,
            )
            result = mg.retrieve_context(query=keywords, k=20, layer="epistemic")
            chunks = result.get("chunks", [])
            graph_nodes = result.get("graph", {}).get("nodes", [])
        except Exception as e:
            logger.warning(f"Relevance retrieval failed, falling back to connectivity ranking: {e}")
            entities.sort(key=lambda e: len(e.related_edges), reverse=True)
            return entities[:max_entities]

        # Step 3: Count entity name mentions in chunks + node labels/summaries
        all_text = " ".join(c.get("content", "") for c in chunks)
        all_text += " " + " ".join(
            n.get("label", "") + " " + n.get("summary", "")
            for n in graph_nodes
        )

        mentions = Counter()
        for name in entity_names:
            count = all_text.count(name)
            if count > 0:
                mentions[name] = count

        # Step 4: Dedup by name (keep the entity with most edges per name)
        best_by_name: Dict[str, 'EntityNode'] = {}
        for entity in entities:
            existing = best_by_name.get(entity.name)
            if not existing or len(entity.related_edges) > len(existing.related_edges):
                best_by_name[entity.name] = entity
        deduped = list(best_by_name.values())

        # Step 5: Score = mentions * 100 + edge_count (mentions dominate)
        scored = []
        for entity in deduped:
            mention_score = mentions.get(entity.name, 0)
            edge_score = len(entity.related_edges)
            scored.append((mention_score * 100 + edge_score, entity))

        scored.sort(key=lambda x: x[0], reverse=True)
        selected = [entity for _, entity in scored[:max_entities]]

        # Log results
        mentioned_count = sum(1 for s, _ in scored[:max_entities] if s >= 100)
        logger.info(
            f"Entity relevance ranking complete: {total} → {len(deduped)} (deduped) → {len(selected)} "
            f"({mentioned_count} topic-relevant, "
            f"{len(selected) - mentioned_count} padded by connectivity)"
        )
        if selected:
            top5 = [e.name for e in selected[:5]]
            logger.info(f"  Top 5: {top5}")

        return selected

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True,
        max_entities: int = 0,
        simulation_requirement: str = "",
        source: str = "upload"
    ) -> FilteredEntities:
        """
        Filter nodes matching predefined entity types

        Filtering logic:
        - MindGraph nodes have a node_type field; check if it's a meaningful entity type
        - Filter out generic types (Entity, Node, Unknown, Snippet, Chunk and other base types)
        - For native MindGraph entities (node_type="Entity"), check props.entity_type as the actual type

        Args:
            graph_id: Graph ID
            defined_entity_types: List of predefined entity types
            enrich_with_edges: Whether to get related edge info for each entity
            source: Data source - "upload" or "mindgraph"

        Returns:
            FilteredEntities: Filtered entity collection
        """
        logger.info(f"Starting entity filtering for graph {graph_id} (source={source})...")

        # For large MindGraph graphs: use retrieval-first approach instead of
        # loading all nodes (which is slow and capped at 2000 for graphs with
        # tens of thousands of nodes).
        if source == "mindgraph" and max_entities > 0 and simulation_requirement:
            result = self._select_entities_by_retrieval(
                graph_id, simulation_requirement, max_entities, defined_entity_types
            )
            if result is not None:
                return result
            logger.info("Semantic entity search returned None, falling back to paginated approach")

        # Get nodes
        # MindGraph mode: only get Entity type nodes (server-side filtering, significantly reducing data volume)
        if source == "mindgraph":
            logger.info("MindGraph mode: fetching Entity type nodes only...")
            mg_nodes = self.client.list_all_graph_nodes(node_type="Entity")
            all_nodes = [self._normalize_node(n) for n in mg_nodes]
        else:
            all_nodes = self.get_all_nodes(graph_id, source=source)
        total_count = len(all_nodes)

        # Build node UUID to node data mapping
        node_map = {n["uuid"]: n for n in all_nodes}

        # ========== Filter entity nodes ==========
        #
        # MindGraph has 55+ node types across 6 cognitive layers.
        # Only "Entity" nodes represent real-world entities (people, orgs, places).
        # Everything else (Claim, Concept, Hypothesis, Pattern, Theory, etc.)
        # is knowledge structure and should NOT become agent personas.
        #
        # Strategy:
        #   source="mindgraph" → allowlist: only keep node_type="Entity"
        #   source="upload"    → blocklist: exclude known infrastructure types
        #                        (upload mode uses custom ontology types as node_type)

        if source == "mindgraph":
            # Allowlist: only Entity nodes from the MindGraph graph
            entity_candidates = []
            for node in all_nodes:
                labels = node.get("labels", [])
                if "Entity" in labels:
                    entity_candidates.append(node)
            logger.info(f"MindGraph mode: {len(entity_candidates)}/{total_count} Entity nodes")
        else:
            # Blocklist: exclude infrastructure types (upload mode)
            BASE_TYPES = {
                "Entity", "Node", "Unknown", "Snippet", "Chunk", "Source",
                "Document", "Observation", "Trace", "Session", "Journal",
                "Summary", "Preference", "MemoryPolicy",
                # Epistemic layer
                "Claim", "Evidence", "Warrant", "Argument", "Hypothesis",
                "Theory", "Paradigm", "Anomaly", "Method", "Experiment",
                "Concept", "Assumption", "Question", "OpenQuestion",
                "Analogy", "Pattern", "Mechanism", "Model", "ModelEvaluation",
                "InferenceChain", "SensitivityAnalysis", "ReasoningStrategy",
                "Theorem", "Equation",
                # Intent layer
                "Goal", "Project", "Decision", "Option", "Constraint", "Milestone",
                # Action layer
                "Affordance", "Flow", "FlowStep", "Control", "RiskAssessment",
                # Agent layer
                "Agent", "Task", "Plan", "PlanStep", "Approval", "Policy",
                "Execution", "SafetyBudget",
            }
            entity_candidates = []
            for node in all_nodes:
                labels = node.get("labels", [])
                custom_labels = [l for l in labels if l not in BASE_TYPES]
                if custom_labels:
                    entity_candidates.append(node)
            logger.info(f"Upload mode: {len(entity_candidates)}/{total_count} custom type nodes")

        # Get all edges (only when there are candidate entities and edge info is needed)
        all_edges = []
        if entity_candidates and enrich_with_edges:
            all_edges = self.get_all_edges(graph_id, source=source)

        # ========== Classify entity_type and filter ==========
        #
        # MindGraph Entity nodes can represent anything extracted from text:
        # people, organizations, concepts, events, places, policies, etc.
        # Only people and organizations can become agent personas.

        # Log entity_type distribution for debugging
        if source == "mindgraph":
            type_counts: Dict[str, int] = {}
            for node in entity_candidates:
                et = node.get("attributes", {}).get("entity_type", "Unknown")
                type_counts[et] = type_counts.get(et, 0) + 1
            logger.info(f"Entity type distribution: {dict(sorted(type_counts.items(), key=lambda x: -x[1]))}")

        filtered_entities = []
        entity_types_found = set()
        skipped_types: Dict[str, int] = {}

        for node in entity_candidates:
            labels = node.get("labels", [])
            attributes = node.get("attributes", {})

            if source == "mindgraph":
                # MindGraph Entity nodes: use props.entity_type as the real type
                props_entity_type = attributes.get("entity_type", "")
                entity_type = props_entity_type or "Entity"

                # Filter: only keep agent-compatible entities for simulation
                if not defined_entity_types and not self._is_agent_compatible(entity_type):
                    skipped_types[entity_type] = skipped_types.get(entity_type, 0) + 1
                    continue
            else:
                # Upload mode: use the custom label as entity type
                BASE_TYPES_UPLOAD = {
                    "Entity", "Node", "Unknown", "Snippet", "Chunk", "Source",
                    "Document", "Observation", "Trace", "Session", "Journal",
                    "Summary", "Preference", "MemoryPolicy",
                }
                custom_labels = [l for l in labels if l not in BASE_TYPES_UPLOAD]
                entity_type = custom_labels[0] if custom_labels else "Unknown"

            # If predefined types are specified, check for a match
            if defined_entity_types:
                if entity_type not in defined_entity_types:
                    continue

            entity_types_found.add(entity_type)

            # Create entity node object
            # Ensure labels contain the resolved entity_type (for downstream use)
            entity_labels = list(labels)
            if entity_type and entity_type not in entity_labels:
                entity_labels.append(entity_type)

            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=entity_labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )

            # Get related edges and nodes
            if enrich_with_edges:
                related_edges = []
                related_node_uuids = set()

                for edge in all_edges:
                    if edge["source_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    elif edge["target_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])

                entity.related_edges = related_edges

                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_map:
                        related_node = node_map[related_uuid]
                        related_nodes.append({
                            "uuid": related_node["uuid"],
                            "name": related_node["name"],
                            "labels": related_node["labels"],
                            "summary": related_node.get("summary", ""),
                            "attributes": related_node.get("attributes", {}),
                        })

                entity.related_nodes = related_nodes

            filtered_entities.append(entity)

        if skipped_types:
            logger.info(f"Skipped non-agent entity types: {dict(sorted(skipped_types.items(), key=lambda x: -x[1]))}")

        # Rank and limit if requested
        if max_entities > 0 and len(filtered_entities) > max_entities:
            if simulation_requirement:
                # Relevance-based ranking: retrieve epistemic context for the
                # requirement, count which entities appear in the results
                filtered_entities = self._rank_by_relevance(
                    filtered_entities, simulation_requirement, max_entities
                )
            else:
                # Fallback: rank by edge count
                filtered_entities.sort(key=lambda e: len(e.related_edges), reverse=True)
                filtered_entities = filtered_entities[:max_entities]
                logger.info(
                    f"Ranked by graph connectivity: top {max_entities} "
                    f"(edge count: {len(filtered_entities[-1].related_edges)}-{len(filtered_entities[0].related_edges)})"
                )

        logger.info(f"Filtering complete: total nodes {total_count}, matched {len(filtered_entities)}, "
                   f"entity types: {entity_types_found}")

        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )

    def get_entity_with_context(
        self,
        graph_id: str,
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        Get a single entity with its full context (edges and associated nodes)
        """
        try:
            mg_node = self.client.get_node(uid=entity_uuid)
            if not mg_node:
                return None

            node = self._normalize_node(mg_node)

            # Get edges for the node
            edges = self.get_node_edges(entity_uuid)

            # Get all nodes for association lookup
            all_nodes = self.get_all_nodes(graph_id)
            node_map_data = {n["uuid"]: n for n in all_nodes}

            # Process related edges and nodes
            related_edges = []
            related_node_uuids = set()

            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])

            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map_data:
                    rn = node_map_data[related_uuid]
                    related_nodes.append({
                        "uuid": rn["uuid"],
                        "name": rn["name"],
                        "labels": rn["labels"],
                        "summary": rn.get("summary", ""),
                    })

            return EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=node["labels"],
                summary=node["summary"],
                attributes=node["attributes"],
                related_edges=related_edges,
                related_nodes=related_nodes,
            )

        except Exception as e:
            logger.error(f"Failed to get entity {entity_uuid}: {str(e)}")
            return None

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True,
        source: str = "upload"
    ) -> List[EntityNode]:
        """Get all entities of a specified type"""
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges,
            source=source
        )
        return result.entities
