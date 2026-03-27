"""
图谱构建服务
使用MindGraph API构建知识图谱
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
    """图谱信息"""
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
    图谱构建服务
    负责调用MindGraph API构建知识图谱
    """

    # 同步摄入的文本块最大字符数（MindGraph sync chunk limit）
    SYNC_CHUNK_MAX_CHARS = 7500

    def __init__(self):
        if not Config.MINDGRAPH_API_KEY:
            raise ValueError("MINDGRAPH_API_KEY 未配置")

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
        异步构建图谱

        Args:
            text: 输入文本
            ontology: 本体定义（来自接口1的输出）
            graph_name: 图谱名称
            chunk_size: 文本块大小
            chunk_overlap: 块重叠大小
            batch_size: 每批发送的块数量

        Returns:
            任务ID
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
        """图谱构建工作线程"""
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message="开始构建图谱..."
            )

            # 1. 创建图谱（生成命名空间ID）
            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=f"图谱已创建: {graph_id}"
            )

            # 2. 设置本体（MindGraph有内置类型，此步骤为轻量级元数据记录）
            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message="本体已设置"
            )

            # 3. 文本分块
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            total_chunks = len(chunks)
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=f"文本已分割为 {total_chunks} 个块"
            )

            # 4. 分批发送数据到MindGraph
            job_ids = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 40),  # 20-60%
                    message=msg
                )
            )

            # 5. 等待MindGraph处理完成
            self.task_manager.update_task(
                task_id,
                progress=60,
                message="等待MindGraph处理数据..."
            )

            self._wait_for_jobs(
                job_ids,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 30),  # 60-90%
                    message=msg
                )
            )

            # 6. 获取图谱信息
            self.task_manager.update_task(
                task_id,
                progress=90,
                message="获取图谱信息..."
            )

            graph_info = self._get_graph_info(graph_id)

            # 完成
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
        创建图谱（生成命名空间ID）

        MindGraph没有per-graph隔离，所以graph_id仅作为agent_id命名空间使用。
        不需要调用MindGraph API创建"图谱"——命名空间由agent_id参数隐式创建。
        """
        graph_id = f"mirofish_{uuid.uuid4().hex[:16]}"
        logger.info(f"创建图谱命名空间: {graph_id}, name={name}")
        return graph_id

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """
        设置图谱本体

        MindGraph有56种内置节点类型，不需要动态创建Pydantic类。
        我们为每个本体定义的实体类型预创建"锚点"Entity节点，
        帮助MindGraph的实体解析在自动提取时正确匹配类型。
        """
        entity_types = ontology.get("entity_types", [])
        edge_types = ontology.get("edge_types", [])

        # 预创建实体类型锚点，帮助MindGraph的entity resolution
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
                logger.warning(f"创建实体类型锚点失败 ({name}): {e}")

        logger.info(
            f"本体已设置: {len(entity_types)} 个实体类型 ({created} 个锚点已创建), "
            f"{len(edge_types)} 个关系类型"
        )

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None
    ) -> List[str]:
        """
        分批添加文本到MindGraph，返回异步任务的job_id列表

        对于短文本块（<7500字符）使用同步 /ingest/chunk 接口；
        对于长文本块使用异步 /ingest/document 接口。
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
                    f"发送第 {batch_num}/{total_batches} 批数据 ({len(batch_chunks)} 块)...",
                    progress
                )

            # 种子文档使用 reality+epistemic 层提取实体和关系
            seed_layers = ["reality", "epistemic"]

            # 并行提交batch内的chunks
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
                        result = future.result()  # 失败时抛出异常
                        if isinstance(result, str) and result:
                            # ingest_document 返回 job_id 字符串
                            job_ids.append(result)
                        elif isinstance(result, dict):
                            job_id = result.get("job_id")
                            if job_id:
                                job_ids.append(job_id)
            except Exception as e:
                if progress_callback:
                    progress_callback(f"批次 {batch_num} 发送失败: {str(e)}", 0)
                raise

            # 避免请求过快
            time.sleep(0.5)

        return job_ids

    def _wait_for_jobs(
        self,
        job_ids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600
    ):
        """等待所有异步摄入任务完成"""
        if not job_ids:
            if progress_callback:
                progress_callback("所有文本块已同步处理完成", 1.0)
            return

        start_time = time.time()
        pending_jobs = set(job_ids)
        completed_count = 0
        total_jobs = len(job_ids)

        if progress_callback:
            progress_callback(f"开始等待 {total_jobs} 个异步任务处理...", 0)

        while pending_jobs:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        f"部分任务超时，已完成 {completed_count}/{total_jobs}",
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
                            logger.warning(f"摄入任务 {job_id} 状态: {status}")
                            completed_count += 1  # 仍然算进度

                except Exception:
                    pass

            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    f"MindGraph处理中... {completed_count}/{total_jobs} 完成, "
                    f"{len(pending_jobs)} 待处理 ({elapsed}秒)",
                    completed_count / total_jobs if total_jobs > 0 else 0
                )

            if pending_jobs:
                time.sleep(3)

        if progress_callback:
            progress_callback(f"处理完成: {completed_count}/{total_jobs}", 1.0)

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """获取图谱信息"""
        # 先尝试实体去重
        self._deduplicate_entities(graph_id)

        nodes = self.client.list_all_nodes(project_id=graph_id)
        edges = self.client.list_all_edges(project_id=graph_id)

        # 统计实体类型
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
        利用MindGraph的模糊实体解析尝试合并重复实体

        MindGraph的built-in alias matching和fuzzy_resolve会在摄入时
        自动去重大部分实体。此方法记录最终的实体分布供日志使用。
        """
        try:
            nodes = self.client.list_all_nodes(project_id=graph_id)
            entity_nodes = [
                n for n in nodes
                if n.get("node_type", n.get("type", "")) == "Entity"
            ]
            logger.info(
                f"实体去重检查: {len(entity_nodes)} 个Entity节点 / "
                f"{len(nodes)} 个总节点 (MindGraph自动去重)"
            )
        except Exception as e:
            logger.debug(f"实体去重检查失败: {e}")

    def get_graph_data(self, graph_id: str, source: str = "upload") -> Dict[str, Any]:
        """
        获取完整图谱数据（包含详细信息）

        返回格式与前端兼容（保持原有API契约）

        Args:
            graph_id: 图谱ID
            source: "upload"（按agent_id过滤）或 "mindgraph"（读取全量图谱）
        """
        if source == "mindgraph":
            nodes = self.client.list_all_graph_nodes()
            edges = self.client.list_all_graph_edges(nodes=nodes)
        else:
            nodes = self.client.list_all_nodes(project_id=graph_id)
            edges = self.client.list_all_edges(project_id=graph_id)

        # 创建节点映射用于获取节点名称
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
        """删除图谱（清除该命名空间下的所有数据）"""
        self.client.delete_project_data(project_id=graph_id)
