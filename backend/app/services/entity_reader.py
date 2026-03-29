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

        # Step 1: Summarize requirement into keywords
        try:
            from .graph_context_provider import GraphContextProvider
            keywords = GraphContextProvider._summarize_feed(requirement)
            if not keywords:
                keywords = requirement[:50]
                logger.info("Requirement summarization failed, using first 50 chars of raw text")
        except Exception as e:
            keywords = requirement[:50]
            logger.warning(f"Requirement summarization error: {e}")

        logger.info(f"Entity relevance ranking: query='{keywords[:80]}...'")

        # Step 2: Retrieve epistemic context
        try:
            from mindgraph import MindGraph
            import os
            mg = MindGraph(
                os.environ.get("MINDGRAPH_BASE_URL", "https://api.mindgraph.cloud"),
                api_key=os.environ.get("MINDGRAPH_API_KEY", ""),
                timeout=120,
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

        # Only person/human and organization entities can become agent personas.
        # MindGraph entity_type docs: "Type of entity (person, org, technology, etc.)"
        # — concepts, events, places, technologies etc. are also stored as Entity nodes.
        _AGENT_TYPE_KEYWORDS = {"person", "human", "org"}

        def _is_agent_compatible(entity_type_str: str) -> bool:
            """Check if entity_type is a person or organization."""
            if not entity_type_str:
                return False
            lower = entity_type_str.lower()
            return any(kw in lower for kw in _AGENT_TYPE_KEYWORDS)

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

                # Filter: only keep person/organization entities for agent simulation
                if not defined_entity_types and not _is_agent_compatible(entity_type):
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
