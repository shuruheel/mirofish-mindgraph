"""
MindGraph client wrapper
Unified access layer based on mindgraph-sdk with project-level namespace isolation and retry mechanism

MindGraph has no per-graph isolation (one API Key = one organization-level graph),
so namespace isolation is achieved via agent_id:
- Write: all requests carry agent_id=project_id
- Read: filter via get_agent_nodes(agent_id)
"""

import threading
import time
from typing import Any, Dict, List, Optional

import httpx
from mindgraph import MindGraph, MindGraphError

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.mindgraph_client')


class MindGraphClient:
    """
    MindGraph REST API client (based on mindgraph-sdk)

    Core responsibilities:
    1. Wrap mindgraph-sdk with retry mechanism
    2. agent_id namespace isolation
    3. High-level convenience methods (agent post ingestion, decision recording, etc.)
    4. Concurrency control - limit concurrent API calls to avoid server overload
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # seconds, exponential backoff
    MAX_CONCURRENT_CALLS = 5  # max concurrent API calls

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or Config.MINDGRAPH_API_KEY
        self.base_url = (base_url or Config.MINDGRAPH_BASE_URL).rstrip('/')
        if not self.api_key:
            raise ValueError("MINDGRAPH_API_KEY not configured")
        self._mg = MindGraph(self.base_url, api_key=self.api_key, timeout=60.0)
        self._semaphore = threading.Semaphore(self.MAX_CONCURRENT_CALLS)
        logger.info(f"MindGraphClient initialized: base_url={self.base_url}")

    def close(self):
        """Close underlying httpx connection pool"""
        self._mg.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ═══════════════════════════════════════
    # Retry wrapper
    # ═══════════════════════════════════════

    def _with_retry(self, func, *args, operation_name: str = "API call", **kwargs) -> Any:
        """Call wrapper with exponential backoff retry and concurrency control"""
        self._semaphore.acquire()
        try:
            return self._with_retry_inner(func, *args, operation_name=operation_name, **kwargs)
        finally:
            self._semaphore.release()

    def _with_retry_inner(self, func, *args, operation_name: str = "API call", **kwargs) -> Any:
        """Call wrapper with exponential backoff retry (internal method, no locking)"""
        delay = self.RETRY_DELAY
        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except MindGraphError as e:
                last_exception = e
                # Don't retry client errors (4xx), except 429
                if 400 <= e.status < 500 and e.status != 429:
                    logger.error(f"MindGraph {operation_name} client error ({e.status}): {e}")
                    raise
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(
                        f"MindGraph {operation_name} attempt {attempt + 1} failed ({e.status}): "
                        f"{str(e)[:100]}, retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"MindGraph {operation_name} still failed after {self.MAX_RETRIES} attempts: {e}")
            except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(
                        f"MindGraph {operation_name} connection attempt {attempt + 1} failed: "
                        f"{str(e)[:100]}, retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"MindGraph {operation_name} still failed after {self.MAX_RETRIES} attempts: {e}")

        raise last_exception

    # ═══════════════════════════════════════
    # Document ingestion
    # ═══════════════════════════════════════

    def ingest_document(self, text: str, project_id: str, source_name: str = "",
                        layers: Optional[List[str]] = None) -> str:
        """
        Async document ingestion (auto-chunking, entity/relation extraction)

        Returns:
            job_id: Async task ID
        """
        result = self._with_retry(
            self._mg.ingest_document,
            content=text,
            agent_id=project_id,
            title=source_name or None,
            layers=layers,
            operation_name=f"document ingestion(project={project_id})",
        )
        job_id = result.get("job_id", "")
        logger.info(f"Document ingestion task submitted: job_id={job_id}, project={project_id}")
        return job_id

    def ingest_chunk(self, text: str, project_id: str,
                     layers: Optional[List[str]] = None,
                     label: Optional[str] = None,
                     chunk_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Synchronous chunk ingestion (<8000 chars, returns extraction result immediately)
        """
        result = self._with_retry(
            self._mg.ingest_chunk,
            content=text,
            agent_id=project_id,
            layers=layers,
            label=label,
            chunk_type=chunk_type,
            operation_name=f"chunk ingestion(project={project_id})",
        )
        logger.debug(f"Chunk ingestion complete: project={project_id}, text_len={len(text)}")
        return result

    def poll_job(self, job_id: str, timeout: int = 600, poll_interval: float = 3.0) -> Dict[str, Any]:
        """Poll async ingestion task status"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            result = self._with_retry(
                self._mg.get_job, job_id,
                operation_name=f"poll job({job_id[:12]})",
            )
            status = result.get("status", "")

            if status == "completed":
                logger.info(f"Ingestion task completed: job_id={job_id}")
                return result
            elif status == "failed":
                error = result.get("error", "unknown error")
                logger.error(f"Ingestion task failed: job_id={job_id}, error={error}")
                raise RuntimeError(f"MindGraph ingestion task failed: {error}")
            elif status == "cancelled":
                raise RuntimeError(f"MindGraph ingestion task cancelled: {job_id}")

            elapsed = int(time.time() - start_time)
            progress = result.get("progress", {})
            logger.debug(f"Ingestion task in progress: job_id={job_id}, status={status}, elapsed={elapsed}s, progress={progress}")
            time.sleep(poll_interval)

        raise TimeoutError(f"Timed out waiting for MindGraph ingestion task ({timeout}s): job_id={job_id}")

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """Get task status (no polling)"""
        return self._with_retry(
            self._mg.get_job, job_id,
            operation_name=f"check job({job_id[:12]})",
        )

    # ═══════════════════════════════════════
    # Search and retrieval
    # ═══════════════════════════════════════

    def search_hybrid(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """
        Hybrid search (BM25 + semantic vector + RRF fusion)

        Returns:
            {"results": [...]}
        """
        # SDK's hybrid_search doesn't support agent_id, use retrieve to pass directly
        results = self._with_retry(
            self._mg.retrieve,
            action="hybrid",
            query=query,
            limit=limit,
            agent_id=project_id,
            operation_name=f"hybrid search(query={query[:30]}...)",
        )
        # SDK returns list, normalize to dict
        if isinstance(results, list):
            results = {"results": results}
        logger.info(f"Search complete: found {len(results.get('results', []))} results")
        return results

    def search_text(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """Full-text search (BM25)"""
        results = self._with_retry(
            self._mg.retrieve,
            action="text",
            query=query,
            limit=limit,
            agent_id=project_id,
            operation_name=f"text search(query={query[:30]}...)",
        )
        if isinstance(results, list):
            results = {"results": results}
        return results

    def search_semantic(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """Semantic search - degrades to hybrid (semantic requires embedding config)"""
        return self.search_hybrid(query, project_id, limit)

    def semantic_search(self, query: str, k: int = 50,
                        node_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Semantic node search via POST /retrieve with action="semantic".

        Single HNSW query filtered by node_type. Returns nodes ranked by
        cosine similarity — no chunk tracing, no graph traversal.
        """
        body: Dict[str, Any] = {
            "action": "semantic",
            "query": query,
            "k": k,
        }
        if node_types is not None:
            body["node_types"] = node_types
        return self._with_retry(
            self._mg.retrieve,
            **body,
            operation_name=f"semantic search(query={query[:30]}..., k={k})",
        )

    def retrieve_context(self, query: str, project_id: Optional[str] = None,
                         k: int = 5, depth: int = 1,
                         include_chunks: Optional[bool] = None,
                         node_types: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Graph-augmented RAG retrieval

        SDK v0.1.4+ provides native retrieve_context() method that searches the entire graph.
        When project_id is specified, agent_id is passed via _request for namespace filtering.
        When project_id is None, uses SDK native method to search the full graph.
        """
        if project_id:
            # With agent_id namespace filtering
            body: Dict[str, Any] = {
                "query": query,
                "k": k,
                "depth": depth,
                "agent_id": project_id,
            }
            if include_chunks is not None:
                body["include_chunks"] = include_chunks
            if node_types is not None:
                body["node_types"] = node_types
            return self._with_retry(
                self._mg._request, "POST", "/retrieve/context", body,
                operation_name=f"RAG retrieval(query={query[:30]}..., k={k})",
            )
        else:
            # Search entire graph (MindGraph connection mode)
            kwargs: Dict[str, Any] = {"query": query, "k": k, "depth": depth}
            if include_chunks is not None:
                kwargs["include_chunks"] = include_chunks
            if node_types is not None:
                kwargs["node_types"] = node_types
            return self._with_retry(
                self._mg.retrieve_context,
                **kwargs,
                operation_name=f"RAG global retrieval(query={query[:30]}..., k={k})",
            )

    # ═══════════════════════════════════════
    # Cognitive queries
    # ═══════════════════════════════════════

    def get_weak_claims(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get low-confidence claims

        Uses SDK's dedicated GET /claims/weak endpoint.
        Note: this endpoint does not support agent_id filtering, returns global results.
        """
        results = self._with_retry(
            self._mg.get_weak_claims,
            operation_name="get weak claims",
        )
        if isinstance(results, list):
            return {"results": results[:limit]}
        return results

    def get_contradictions(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get unresolved contradictions

        Uses SDK's dedicated GET /contradictions endpoint.
        Note: this endpoint does not support agent_id filtering, returns global results.
        """
        results = self._with_retry(
            self._mg.get_contradictions,
            operation_name="get contradictions",
        )
        if isinstance(results, list):
            return {"results": results[:limit]}
        return results

    def get_open_questions(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get open questions

        Uses SDK's dedicated GET /questions endpoint.
        Note: this endpoint does not support agent_id filtering, returns global results.
        """
        results = self._with_retry(
            self._mg.get_open_questions,
            operation_name="get open questions",
        )
        if isinstance(results, list):
            return {"results": results[:limit]}
        return results

    # ═══════════════════════════════════════
    # Node/edge listing
    # ═══════════════════════════════════════

    def list_nodes(
        self,
        project_id: str,
        node_type: Optional[str] = None,
        layer: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List nodes with pagination"""
        result = self._with_retry(
            self._mg.get_nodes,
            node_type=node_type,
            layer=layer,
            limit=limit,
            offset=offset,
            operation_name=f"list nodes(project={project_id})",
        )
        return result.get("items", result) if isinstance(result, dict) else result

    def list_all_graph_nodes(self, node_type: Optional[str] = None,
                            layer: Optional[str] = None, max_items: int = 2000) -> List[Dict[str, Any]]:
        """
        Get all nodes from the entire graph (no agent_id filtering)

        Used for connecting to existing MindGraph graphs, reading full graph data
        built by users through MindGraph Cloud.
        """
        all_nodes = []
        offset = 0
        page_size = 100

        while len(all_nodes) < max_items:
            batch = self.list_nodes(
                project_id="__global__",  # For logging only, does not affect query
                node_type=node_type,
                layer=layer,
                limit=page_size,
                offset=offset,
            )
            if not batch:
                break
            all_nodes.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        logger.info(f"Full graph node read complete: {len(all_nodes)} nodes total")
        return all_nodes[:max_items]

    def list_all_graph_edges(self, nodes: Optional[List[Dict[str, Any]]] = None,
                            max_items: int = 5000) -> List[Dict[str, Any]]:
        """
        Get graph edges (no agent_id filtering)

        Uses POST /edges/batch bulk API to get all edges between nodes in a single request.

        Args:
            nodes: Pre-fetched node list (to avoid duplicate queries)
            max_items: Maximum number of edges to return
        """
        if nodes is None:
            nodes = self.list_all_graph_nodes()

        node_uids = [n.get("uid", "") for n in nodes if n.get("uid")]
        if not node_uids:
            return []

        logger.info(f"Batch querying edges between {len(node_uids)} nodes...")

        try:
            edges = self._with_retry(
                self._mg.get_edges_batch, node_uids,
                operation_name=f"batch query edges({len(node_uids)} nodes)",
            )
            logger.info(f"Full graph edge read complete: {len(edges)} edges total")
            return edges[:max_items]
        except Exception as e:
            logger.warning(f"Batch edge query failed, falling back to per-node query: {e}")
            # Fallback: per-node query (limit count to avoid timeout)
            all_edges = []
            seen_uids = set()
            for node_uid in node_uids[:200]:
                try:
                    node_edges = self._with_retry(
                        self._mg.get_edges, from_uid=node_uid,
                        operation_name=f"list edges(from={node_uid[:12]})",
                    )
                    for edge in node_edges:
                        edge_uid = edge.get("uid", "")
                        if edge_uid and edge_uid not in seen_uids:
                            seen_uids.add(edge_uid)
                            all_edges.append(edge)
                except Exception:
                    pass
            logger.info(f"Fallback edge query complete: {len(all_edges)} edges total")
            return all_edges[:max_items]

    def list_all_nodes(self, project_id: str, node_type: Optional[str] = None,
                       layer: Optional[str] = None, max_items: int = 2000) -> List[Dict[str, Any]]:
        """
        Get all nodes for a project

        Prefers get_agent_nodes(agent_id) for namespace filtering.
        Falls back to full paginated query if that endpoint is unavailable.
        """
        try:
            nodes = self._with_retry(
                self._mg.get_agent_nodes, project_id,
                operation_name=f"get agent nodes(project={project_id})",
            )
            # Filter by node_type
            if node_type:
                nodes = [n for n in nodes if n.get("node_type") == node_type]
            return nodes[:max_items]
        except Exception as e:
            logger.debug(f"get_agent_nodes failed, falling back to paginated query: {e}")

        # Fallback: full pagination
        all_nodes = []
        offset = 0
        page_size = 100

        while len(all_nodes) < max_items:
            batch = self.list_nodes(
                project_id=project_id,
                node_type=node_type,
                layer=layer,
                limit=page_size,
                offset=offset,
            )
            if not batch:
                break
            all_nodes.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        return all_nodes[:max_items]

    def list_all_edges(self, project_id: str) -> List[Dict[str, Any]]:
        """
        Get all edges for a project

        Uses POST /edges/batch bulk API to get edges between nodes.
        Falls back to per-node query if bulk API is unavailable.
        """
        nodes = self.list_all_nodes(project_id=project_id)
        node_uids = [n.get("uid", "") for n in nodes if n.get("uid")]
        if not node_uids:
            return []

        try:
            edges = self._with_retry(
                self._mg.get_edges_batch, node_uids,
                operation_name=f"batch query edges({len(node_uids)} nodes, project={project_id})",
            )
            return edges
        except Exception as e:
            logger.debug(f"Batch edge query failed, falling back to per-node query: {e}")

        # Fallback: per-node query
        all_edges = []
        seen_uids = set()

        for node in nodes:
            node_uid = node.get("uid", "")
            if not node_uid:
                continue
            try:
                edges = self._with_retry(
                    self._mg.get_edges, from_uid=node_uid,
                    operation_name=f"list edges(from={node_uid[:12]})",
                )
                for edge in edges:
                    edge_uid = edge.get("uid", "")
                    if edge_uid and edge_uid not in seen_uids:
                        seen_uids.add(edge_uid)
                        all_edges.append(edge)
            except Exception:
                pass

        return all_edges

    # ═══════════════════════════════════════
    # Single node operations
    # ═══════════════════════════════════════

    def get_node(self, uid: str) -> Dict[str, Any]:
        """Get single node details"""
        return self._with_retry(
            self._mg.get_node, uid,
            operation_name=f"get node({uid[:12]})",
        )

    def get_nodes_batch(self, uids: List[str]) -> List[Dict[str, Any]]:
        """Batch get node details (single API call)"""
        if not uids:
            return []
        return self._with_retry(
            self._mg.get_nodes_batch, uids,
            operation_name=f"batch get nodes({len(uids)})",
        )

    def get_edges_batch(self, node_uids: List[str]) -> List[Dict[str, Any]]:
        """Batch get edges between nodes (single API call)"""
        if not node_uids:
            return []
        return self._with_retry(
            self._mg.get_edges_batch, node_uids,
            operation_name=f"batch query edges({len(node_uids)} nodes)",
        )

    def get_neighborhood(self, uid: str, depth: int = 1) -> Dict[str, Any]:
        """
        Get node neighbors (BFS)

        SDK's neighborhood() returns traversal step list (node info).
        Edge data is fetched separately via get_edges(from_uid) and merged into
        {"nodes": [...], "edges": [...]} format.
        """
        result = self._with_retry(
            self._mg.neighborhood, uid, max_depth=depth,
            operation_name=f"get neighborhood({uid[:12]}, depth={depth})",
        )
        nodes = result if isinstance(result, list) else result.get("nodes", [])

        # Get outgoing and incoming edges for this node
        edges = []
        try:
            out_edges = self._with_retry(
                self._mg.get_edges, from_uid=uid,
                operation_name=f"get outgoing edges({uid[:12]})",
            )
            edges.extend(out_edges)
        except Exception:
            pass
        try:
            in_edges = self._with_retry(
                self._mg.get_edges, to_uid=uid,
                operation_name=f"get incoming edges({uid[:12]})",
            )
            # Deduplicate (in case of self-loop edges)
            seen = {e.get("uid") for e in edges}
            for e in in_edges:
                if e.get("uid") not in seen:
                    edges.append(e)
        except Exception:
            pass

        return {"nodes": nodes, "edges": edges}

    def get_node_history(self, uid: str) -> List[Dict[str, Any]]:
        """Get node version history"""
        return self._with_retry(
            self._mg.get_node_history, uid,
            operation_name=f"node history({uid[:12]})",
        )

    # ═══════════════════════════════════════
    # Graph traversal
    # ═══════════════════════════════════════

    def traverse_chain(self, start_uid: str, max_depth: int = 5) -> Dict[str, Any]:
        """Reasoning chain traversal"""
        result = self._with_retry(
            self._mg.reasoning_chain, start_uid, max_depth=max_depth,
            operation_name=f"reasoning chain({start_uid[:12]})",
        )
        if isinstance(result, list):
            return {"chain": result}
        return result

    # ═══════════════════════════════════════
    # Entity management
    # ═══════════════════════════════════════

    def create_entity(self, name: str, entity_type: str, project_id: str,
                      description: str = "", props: Optional[Dict] = None) -> Dict[str, Any]:
        """Explicitly create entity node"""
        entity_props = {
            "entity_type": entity_type,
            "description": description,
            **(props or {})
        }
        return self._with_retry(
            self._mg.find_or_create_entity,
            label=name,
            props=entity_props,
            agent_id=project_id,
            operation_name=f"create entity({name})",
        )

    def resolve_entity(self, name: str, project_id: str) -> Dict[str, Any]:
        """Exact entity name resolution"""
        return self._with_retry(
            self._mg.resolve_entity, name, agent_id=project_id,
            operation_name=f"resolve entity({name})",
        )

    def fuzzy_resolve_entity(self, name: str, project_id: str, limit: int = 5) -> Dict[str, Any]:
        """Fuzzy entity name resolution"""
        return self._with_retry(
            self._mg.fuzzy_resolve_entity, name, limit=limit, agent_id=project_id,
            operation_name=f"fuzzy resolve({name})",
        )

    # ═══════════════════════════════════════
    # Edge creation + Agent registration
    # ═══════════════════════════════════════

    def add_link(self, from_uid: str, to_uid: str, edge_type: str,
                 project_id: Optional[str] = None,
                 agent_id: Optional[str] = None) -> Any:
        """Create generic edge (lightweight)"""
        ns = project_id or agent_id  # Backward compatible with old call style
        return self._with_retry(
            self._mg.add_link,
            from_uid=from_uid, to_uid=to_uid, edge_type=edge_type,
            agent_id=ns,
            operation_name=f"create edge({edge_type})",
        )

    def batch_create(self, nodes: Optional[List[Dict]] = None,
                     edges: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """
        Batch create nodes and/or edges in a single API call.

        Args:
            nodes: List of node dicts, each with {label, props: {_type, ...}, agent_id}
            edges: List of edge dicts, each with {from_uid, to_uid, edge_type}

        Returns:
            {nodes_added, edges_added, node_uids, errors}
        """
        kwargs: Dict[str, Any] = {}
        if nodes:
            kwargs["nodes"] = nodes
        if edges:
            kwargs["edges"] = edges
        if not kwargs:
            return {"nodes_added": 0, "edges_added": 0, "node_uids": [], "errors": []}
        return self._with_retry(
            self._mg.batch,
            operation_name=f"batch create(nodes={len(nodes or [])}, edges={len(edges or [])})",
            **kwargs,
        )

    def add_edge(self, from_uid: str, to_uid: str, edge_type: str,
                 props: Optional[Dict] = None,
                 project_id: Optional[str] = None,
                 agent_id: Optional[str] = None) -> Any:
        """Create edge with properties (SDK auto-injects props._type)"""
        ns = project_id or agent_id  # Backward compatible with old call style
        return self._with_retry(
            self._mg.add_edge,
            from_uid=from_uid, to_uid=to_uid, edge_type=edge_type,
            props=props,
            agent_id=ns,
            operation_name=f"create edge({edge_type})",
        )

    def register_agent_node(self, name: str, project_id: str,
                            summary: str = "",
                            props: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Create Agent node (SDK auto-injects props._type)

        Args:
            name: Agent name
            project_id: Project ID (namespace isolation)
            summary: Agent description
            props: Additional properties (stance, role, influence_weight, etc.)
        """
        node_props = dict(props or {})
        if summary:
            node_props["description"] = summary
        return self._with_retry(
            self._mg.add_node,
            label=name,
            node_type="Agent",
            props=node_props,
            agent_id=project_id,
            operation_name=f"register Agent({name})",
        )

    # ═══════════════════════════════════════
    # Agent post ingestion
    # ═══════════════════════════════════════

    def ingest_agent_post(self, agent_name: str, content: str, project_id: str,
                          platform: str = "", round_num: int = 0) -> Dict[str, Any]:
        """
        Ingest agent post - let MindGraph auto-determine cognitive type

        Return value includes extracted_node_uids for creating AUTHORED edges.
        """
        text = f"{agent_name}: {content}"
        label = f"[{agent_name}] Round {round_num}"
        if platform:
            label = f"[{agent_name}] {platform} R{round_num}"
        return self.ingest_chunk(
            text=text,
            project_id=project_id,
            layers=["reality", "epistemic"],
            label=label,
            chunk_type="agent_post",
        )

    def add_claim(self, text: str, project_id: str, confidence: float = 0.6,
                  evidence_text: Optional[str] = None,
                  agent_name: Optional[str] = None) -> Dict[str, Any]:
        """Add structured claim"""
        claim_label = text[:100]
        if agent_name:
            claim_label = f"{agent_name}: {text[:80]}"

        body: Dict[str, Any] = {
            "claim": {
                "label": claim_label,
                "confidence": confidence,
                "props": {
                    "content": text,
                    "claim_type": "simulation_opinion",
                }
            },
            "agent_id": project_id,
        }
        if agent_name:
            body["claim"]["props"]["proposed_by"] = agent_name
        if evidence_text:
            body["evidence"] = [{
                "label": evidence_text[:100],
                "props": {"description": evidence_text, "evidence_type": "referenced_content"}
            }]

        return self._with_retry(
            self._mg.argue, **body,
            operation_name=f"add claim({agent_name or 'unknown'}, confidence={confidence:.2f})",
        )

    # ═══════════════════════════════════════
    # Memory layer - Session management
    # ═══════════════════════════════════════

    def open_session(self, project_id: str, session_name: str) -> str:
        """Open simulation session"""
        result = self._with_retry(
            self._mg.session,
            action="open",
            label=session_name,
            props={"focus_summary": session_name},
            agent_id=project_id,
            operation_name="open session",
        )
        return result.get("uid", "")

    def trace_session(self, session_uid: str, content: str, project_id: str,
                      trace_type: str = "observation") -> Dict[str, Any]:
        """Add session trace entry"""
        return self._with_retry(
            self._mg.session,
            action="trace",
            session_uid=session_uid,
            label=content[:100],
            props={"content": content, "trace_type": trace_type},
            agent_id=project_id,
            operation_name="session trace",
        )

    def close_session(self, session_uid: str, project_id: str) -> Dict[str, Any]:
        """Close simulation session"""
        return self._with_retry(
            self._mg.session,
            action="close",
            session_uid=session_uid,
            agent_id=project_id,
            operation_name="close session",
        )

    def distill(self, label: str, source_uids: List[str], project_id: str,
                content: str = "") -> Dict[str, Any]:
        """Distill summary"""
        return self._with_retry(
            self._mg.distill,
            label=label,
            summarizes_uids=source_uids,
            props={"content": content},
            agent_id=project_id,
            operation_name="distill summary",
        )

    # ═══════════════════════════════════════
    # Epistemic layer - Hypotheses and anomalies
    # ═══════════════════════════════════════

    def add_hypothesis(self, statement: str, project_id: str,
                       confidence: float = 0.5) -> Dict[str, Any]:
        """Register verifiable hypothesis"""
        return self._with_retry(
            self._mg.inquire,
            action="hypothesis",
            label=statement[:100],
            confidence=confidence,
            props={
                "statement": statement,
                "hypothesis_type": "predictive",
                "status": "proposed",
            },
            agent_id=project_id,
            operation_name=f"register hypothesis(project={project_id})",
        )

    def record_anomaly(self, description: str, project_id: str,
                       severity: str = "medium",
                       agent_name: Optional[str] = None) -> Dict[str, Any]:
        """Record behavioral anomaly"""
        label = description[:100]
        if agent_name:
            label = f"[Anomaly] {agent_name}: {description[:80]}"
        return self._with_retry(
            self._mg.inquire,
            action="anomaly",
            label=label,
            props={
                "description": description,
                "anomaly_type": "behavioral_inconsistency",
                "severity": severity,
            },
            agent_id=project_id,
            operation_name=f"record anomaly({severity})",
        )

    # ═══════════════════════════════════════
    # Epistemic layer - Pattern recognition
    # ═══════════════════════════════════════

    def record_pattern(self, name: str, description: str, project_id: str,
                       instance_count: int = 1) -> Dict[str, Any]:
        """Record emergent pattern"""
        return self._with_retry(
            self._mg.structure,
            action="pattern",
            label=name[:100],
            props={
                "name": name,
                "description": description,
                "pattern_type": "emergent",
                "instance_count": instance_count,
            },
            agent_id=project_id,
            operation_name=f"record pattern({name})",
        )

    # ═══════════════════════════════════════
    # Intent layer - Goals and decisions
    # ═══════════════════════════════════════

    def create_goal(self, label: str, project_id: str,
                    description: str = "", priority: str = "medium",
                    goal_type: str = "social") -> Dict[str, Any]:
        """Register agent goal"""
        return self._with_retry(
            self._mg.commit,
            action="goal",
            label=label[:100],
            props={
                "description": description,
                "priority": priority,
                "goal_type": goal_type,
                "status": "active",
            },
            agent_id=project_id,
            operation_name=f"create goal({label[:30]})",
        )

    def record_decision(self, agent_name: str, description: str,
                        chosen_option: str, rationale: str,
                        project_id: str) -> Dict[str, Any]:
        """
        Record agent's observable decision

        Uses SDK's 3-step convenience methods: open_decision -> add_option -> resolve_decision
        """
        # Step 1: Open decision
        decision = self._with_retry(
            self._mg.open_decision,
            label=description[:100],
            props={"description": description},
            agent_id=project_id,
            operation_name=f"open decision({agent_name})",
        )

        decision_uid = decision.get("uid", "")

        if decision_uid:
            # Step 2: Add chosen option
            option_uid = ""
            try:
                option_result = self._with_retry(
                    self._mg.add_option,
                    decision_uid=decision_uid,
                    label=chosen_option[:100],
                    props={"description": chosen_option},
                    agent_id=project_id,
                    operation_name=f"add option({agent_name})",
                )
                option_uid = option_result.get("uid", "")
            except Exception as e:
                logger.warning(f"Failed to add decision option: {e}")

            # Step 3: Resolve decision
            if option_uid:
                try:
                    self._with_retry(
                        self._mg.resolve_decision,
                        decision_uid=decision_uid,
                        chosen_option_uid=option_uid,
                        summary=rationale,
                        agent_id=project_id,
                        operation_name=f"resolve decision({agent_name})",
                    )
                except Exception as e:
                    logger.warning(f"Failed to resolve decision: {e}")

        return decision

    # ═══════════════════════════════════════
    # Memory layer - Journal
    # ═══════════════════════════════════════

    def create_journal(self, content: str, project_id: str,
                       journal_type: str = "stance", tags: Optional[List[str]] = None,
                       session_uid: Optional[str] = None) -> Dict[str, Any]:
        """Create Journal memory entry (Memory layer)"""
        return self._with_retry(
            self._mg.journal,
            label=content[:100],
            props={
                "content": content,
                "journal_type": journal_type,
                "tags": tags or [],
            },
            session_uid=session_uid,
            agent_id=project_id,
            operation_name=f"create Journal({journal_type})",
        )

    # ═══════════════════════════════════════
    # Reality layer - Observation recording
    # ═══════════════════════════════════════

    def capture_observation(self, content: str, project_id: str,
                            observation_type: str = "simulation_event") -> Dict[str, Any]:
        """
        Record factual observation - create Observation node (Reality layer)

        Uses add_node with node_type="Observation" (low-level CRUD).
        """
        return self._with_retry(
            self._mg.add_node,
            label=content[:100],
            node_type="Observation",
            props={
                "content": content,
                "observation_type": observation_type,
            },
            agent_id=project_id,
            operation_name=f"capture observation({observation_type})",
        )

    # ═══════════════════════════════════════
    # Lifecycle management
    # ═══════════════════════════════════════

    def delete_node(self, uid: str) -> Any:
        """Soft delete node"""
        return self._with_retry(
            self._mg.delete_node, uid,
            operation_name=f"delete node({uid[:12]})",
        )

    def batch_delete_nodes(
        self,
        uids: Optional[List[str]] = None,
        agent_id: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        reason: str = "cleanup",
        hard_purge: bool = False,
    ) -> Dict[str, Any]:
        """
        Batch delete nodes via POST /nodes/delete.

        Soft-deletes (tombstones) nodes and their connected edges.
        At least one of uids, agent_id, or filter must be provided.

        Returns:
            {nodes_tombstoned, edges_tombstoned, nodes_purged, edges_purged}
        """
        body: Dict[str, Any] = {"reason": reason, "hard_purge": hard_purge}
        if uids:
            body["uids"] = uids
        if agent_id:
            body["agent_id"] = agent_id
        if filter:
            body["filter"] = filter
        return self._with_retry(
            self._mg._request, "POST", "/nodes/delete", json=body,
            operation_name=f"batch delete(agent_id={agent_id}, filter={filter})",
        )

    def decay_salience(self, project_id: str, half_life_secs: int = 86400,
                       min_salience: float = 0.1) -> Dict[str, Any]:
        """
        Batch decay salience

        Warning: SDK's decay() is a global operation that affects all project nodes,
        not just the project specified by project_id. project_id is used for logging only.
        """
        logger.warning(
            f"decay_salience is a global operation that will affect all project nodes "
            f"(caller: project={project_id})"
        )
        result = self._with_retry(
            self._mg.decay,
            half_life_secs=half_life_secs,
            min_salience=min_salience,
            operation_name=f"batch decay(caller={project_id})",
        )
        logger.info(f"Batch decay complete: caller={project_id}, result={result}")
        return result if isinstance(result, dict) else {"result": result}

    def delete_project_data(self, project_id: str):
        """
        Delete all data for a project namespace via batch delete.
        """
        logger.info(f"Starting project data deletion: project_id={project_id}")
        result = self.batch_delete_nodes(agent_id=project_id, reason="project_cleanup")
        logger.info(
            f"Project data deletion complete: project_id={project_id}, "
            f"nodes={result.get('nodes_tombstoned', 0)}, edges={result.get('edges_tombstoned', 0)}"
        )

    # ═══════════════════════════════════════
    # Statistics and export
    # ═══════════════════════════════════════

    def get_graph_statistics(self, project_id: str) -> Dict[str, Any]:
        """Get graph statistics"""
        nodes = self.list_all_nodes(project_id=project_id)
        edges = self.list_all_edges(project_id=project_id)

        type_counts: Dict[str, int] = {}
        for node in nodes:
            nt = node.get("node_type", node.get("type", "Unknown"))
            type_counts[nt] = type_counts.get(nt, 0) + 1

        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "type_distribution": type_counts,
        }
