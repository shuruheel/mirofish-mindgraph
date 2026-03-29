"""
Graph construction service
Builds knowledge graphs using the MindGraph API
"""

import uuid
import time
import threading
import concurrent.futures
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.mindgraph_client import MindGraphClient
from .text_processor import TextProcessor
from ..utils.logger import get_logger

logger = get_logger('mirofish.graph_builder')


@dataclass
class GraphInfo:
    """Graph information"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


class GraphBuilderService:
    """
    Graph construction service
    Responsible for calling MindGraph API to build knowledge graphs
    """

    # Max characters for synchronous ingestion text chunks (MindGraph sync chunk limit)
    SYNC_CHUNK_MAX_CHARS = 7500

    def __init__(self):
        if not Config.MINDGRAPH_API_KEY:
            raise ValueError("MINDGRAPH_API_KEY is not configured")

        self.client = MindGraphClient()
        self.task_manager = TaskManager()

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 3
    ) -> str:
        """
        Build graph asynchronously

        Args:
            text: Input text
            ontology: Ontology definition (from interface 1 output)
            graph_name: Graph name
            chunk_size: Text chunk size
            chunk_overlap: Chunk overlap size
            batch_size: Number of chunks per batch

        Returns:
            Task ID
        """
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int
    ):
        """Graph construction worker thread"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="Starting graph construction..."
            )

            # 1. Create graph (generate namespace ID)
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"Graph created: {graph_id}"
            )

            # 2. Set ontology (MindGraph has built-in types; this step is lightweight metadata recording)
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="Ontology set"
            )

            # 3. Text chunking
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"Text split into {total_chunks} chunks"
            )

            # 4. Send data to MindGraph in batches
            job_ids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 40),  # 20-60%
                    message=msg
                )
            )

            # 5. Wait for MindGraph processing to complete
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="Waiting for MindGraph to process data..."
            )

            self._wait_for_jobs(
                job_ids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 30),  # 60-90%
                    message=msg
                )
            )

            # 6. Get graph information
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="Retrieving graph information..."
            )

            graph_info = self._get_graph_info(graph_id)

            # Complete
            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(self, name: str) -> str:
        """
        Create graph (generate namespace ID)

        MindGraph has no per-graph isolation, so graph_id is only used as an agent_id namespace.
        No need to call MindGraph API to create a "graph" -- the namespace is implicitly created via the agent_id parameter.
        """
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        logger.info(f"Created graph namespace: {graph_id}, name={name}")
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        Set graph ontology

        MindGraph has 56 built-in node types, so no need to dynamically create Pydantic classes.
        We pre-create "anchor" Entity nodes for each ontology-defined entity type,
        helping MindGraph's entity resolution correctly match types during automatic extraction.
        """
        entity_types = ontology.get("entity_types", [])
        edge_types = ontology.get("edge_types", [])

        # Pre-create entity type anchors to help MindGraph's entity resolution
        created = 0
        for entity_def in entity_types:
            name = entity_def.get("name", "")
            description = entity_def.get("description", f"A {name} entity type.")
            try:
                self.client.create_entity(
                    name=f"[Type:{name}]",
                    entity_type=name,
                    project_id=graph_id,
                    description=description,
                )
                created += 1
            except Exception as e:
                logger.warning(f"Failed to create entity type anchor ({name}): {e}")

        logger.info(
            f"Ontology set: {len(entity_types)} entity types ({created} anchors created), "
            f"{len(edge_types)} relationship types"
        )

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """
        Add text to MindGraph in batches, returning a list of async job_ids

        Short text chunks (<7500 chars) use the synchronous /ingest/chunk endpoint;
        long text chunks use the async /ingest/document endpoint.
        """
        job_ids = []
        total_chunks = len(chunks)

        for i in range(0, total_chunks, batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size

            if progress_callback:
                progress = (i + len(batch_chunks)) / total_chunks
                progress_callback(
                    f"Sending batch {batch_num}/{total_batches} ({len(batch_chunks)} chunks)...",
                    progress
                )

            # Seed documents use reality+epistemic layers to extract entities and relationships
            seed_layers = ["reality", "epistemic"]

            # Submit chunks within batch in parallel
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                    futures = []
                    for chunk in batch_chunks:
                        if len(chunk) < self.SYNC_CHUNK_MAX_CHARS:
                            futures.append(executor.submit(
                                self.client.ingest_chunk,
                                text=chunk,
                                project_id=graph_id,
                                layers=seed_layers,
                                chunk_type="seed_document",
                            ))
                        else:
                            futures.append(executor.submit(
                                self.client.ingest_document,
                                text=chunk,
                                project_id=graph_id,
                                source_name="",
                                layers=seed_layers,
                            ))

                    for future in concurrent.futures.as_completed(futures):
                        result = future.result()  # Raises exception on failure
                        if isinstance(result, str) and result:
                            # ingest_document returns job_id string
                            job_ids.append(result)
                        elif isinstance(result, dict):
                            job_id = result.get("job_id")
                            if job_id:
                                job_ids.append(job_id)
            except Exception as e:
                if progress_callback:
                    progress_callback(f"Batch {batch_num} sending failed: {str(e)}", 0)
                raise

            # Avoid sending requests too fast
            time.sleep(0.5)

        return job_ids

    def _wait_for_jobs(
        self,
        job_ids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """Wait for all async ingestion tasks to complete"""
        if not job_ids:
            if progress_callback:
                progress_callback("All text chunks processed synchronously", 1.0)
            return

        start_time = time.time()
        pending_jobs = set(job_ids)
        completed_count = 0
        total_jobs = len(job_ids)

        if progress_callback:
            progress_callback(f"Waiting for {total_jobs} async tasks to process...", 0)

        while pending_jobs:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        f"Some tasks timed out, completed {completed_count}/{total_jobs}",
                        completed_count / total_jobs
                    )
                break

            for job_id in list(pending_jobs):
                try:
                    result = self.client.get_job(job_id)
                    status = result.get("status", "")

                    if status in ("completed", "failed", "cancelled"):
                        pending_jobs.remove(job_id)
                        if status == "completed":
                            completed_count += 1
                        else:
                            logger.warning(f"Ingestion task {job_id} status: {status}")
                            completed_count += 1  # Still counts toward progress

                except Exception:
                    pass

            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    f"MindGraph processing... {completed_count}/{total_jobs} completed, "
                    f"{len(pending_jobs)} pending ({elapsed}s)",
                    completed_count / total_jobs if total_jobs > 0 else 0
                )

            if pending_jobs:
                time.sleep(3)

        if progress_callback:
            progress_callback(f"Processing complete: {completed_count}/{total_jobs}", 1.0)

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """Get graph information"""
        # Try entity deduplication first
        self._deduplicate_entities(graph_id)

        nodes = self.client.list_all_nodes(project_id=graph_id)
        edges = self.client.list_all_edges(project_id=graph_id)

        # Count entity types
        entity_types = set()
        for node in nodes:
            node_type = node.get("node_type", node.get("type", ""))
            if node_type and node_type not in ("Entity", "Node", "Unknown"):
                entity_types.add(node_type)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )

    def _deduplicate_entities(self, graph_id: str):
        """
        Attempt to merge duplicate entities using MindGraph's fuzzy entity resolution

        MindGraph's built-in alias matching and fuzzy_resolve automatically
        deduplicate most entities during ingestion. This method logs the final entity distribution.
        """
        try:
            nodes = self.client.list_all_nodes(project_id=graph_id)
            entity_nodes = [
                n for n in nodes
                if n.get("node_type", n.get("type", "")) == "Entity"
            ]
            logger.info(
                f"Entity dedup check: {len(entity_nodes)} Entity nodes / "
                f"{len(nodes)} total nodes (MindGraph auto-dedup)"
            )
        except Exception as e:
            logger.debug(f"Entity dedup check failed: {e}")

    def get_graph_data(self, graph_id: str, source: str = "upload") -> Dict[str, Any]:
        """
        Get complete graph data (with detailed information)

        Return format is frontend-compatible (maintains existing API contract)

        Args:
            graph_id: Graph ID
            source: "upload" (filter by agent_id) or "mindgraph" (read full graph)
        """
        if source == "mindgraph":
            nodes = self.client.list_all_graph_nodes()
            edges = self.client.list_all_graph_edges(nodes=nodes)
        else:
            nodes = self.client.list_all_nodes(project_id=graph_id)
            edges = self.client.list_all_edges(project_id=graph_id)

        # Create node mapping for resolving node names
        node_map = {}
        for node in nodes:
            uid = node.get("uid", "")
            node_map[uid] = node.get("label", node.get("name", ""))

        nodes_data = []
        for node in nodes:
            node_type = node.get("node_type", node.get("type", "Entity"))
            created_at = node.get("created_at")
            if created_at:
                created_at = str(created_at)

            nodes_data.append({
                "uuid": node.get("uid", ""),
                "name": node.get("label", node.get("name", "")),
                "labels": [node_type] if node_type else ["Entity"],
                "summary": node.get("summary", "") or node.get("props", {}).get("content", ""),
                "attributes": node.get("props", {}),
                "created_at": created_at,
            })

        edges_data = []
        for edge in edges:
            created_at = edge.get("created_at")

            source_uid = edge.get("from_uid", edge.get("source_uid", ""))
            target_uid = edge.get("to_uid", edge.get("target_uid", ""))

            edges_data.append({
                "uuid": edge.get("uid", ""),
                "name": edge.get("edge_type", edge.get("type", "")),
                "fact": edge.get("label", edge.get("content", "")),
                "fact_type": edge.get("edge_type", edge.get("type", "")),
                "source_node_uuid": source_uid,
                "target_node_uuid": target_uid,
                "source_node_name": node_map.get(source_uid, ""),
                "target_node_name": node_map.get(target_uid, ""),
                "attributes": edge.get("props", {}),
                "created_at": str(created_at) if created_at else None,
                "valid_at": None,
                "invalid_at": None,
                "expired_at": None,
                "episodes": [],
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str):
        """Delete graph (clear all data under this namespace)"""
        self.client.delete_project_data(project_id=graph_id)
