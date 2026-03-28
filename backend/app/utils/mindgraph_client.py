"""
MindGraph客户端封装
基于 mindgraph-sdk 的统一访问层，带项目级命名空间隔离和重试机制

MindGraph没有per-graph隔离（一个API Key = 一个组织级图谱），
因此通过agent_id实现项目级命名空间：
- 写入时：所有请求携带 agent_id=project_id
- 读取时：通过 get_agent_nodes(agent_id) 过滤
"""

import time
from typing import Any, Dict, List, Optional

import httpx
from mindgraph import MindGraph, MindGraphError

from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.mindgraph_client')


class MindGraphClient:
    """
    MindGraph REST API 客户端（基于 mindgraph-sdk）

    核心职责：
    1. 封装 mindgraph-sdk 并添加重试机制
    2. agent_id命名空间隔离
    3. 高层便捷方法（Agent发言摄入、决策记录等）
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # 秒，指数退避

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or Config.MINDGRAPH_API_KEY
        self.base_url = (base_url or Config.MINDGRAPH_BASE_URL).rstrip('/')
        if not self.api_key:
            raise ValueError("MINDGRAPH_API_KEY 未配置")
        self._mg = MindGraph(self.base_url, api_key=self.api_key, timeout=60.0)
        logger.info(f"MindGraphClient 初始化完成: base_url={self.base_url}")

    def close(self):
        """关闭底层httpx连接池"""
        self._mg.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ═══════════════════════════════════════
    # 重试封装
    # ═══════════════════════════════════════

    def _with_retry(self, func, *args, operation_name: str = "API call", **kwargs) -> Any:
        """带指数退避重试的调用封装"""
        delay = self.RETRY_DELAY
        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except MindGraphError as e:
                last_exception = e
                # 不重试客户端错误(4xx)，除了429
                if 400 <= e.status < 500 and e.status != 429:
                    logger.error(f"MindGraph {operation_name} 客户端错误 ({e.status}): {e}")
                    raise
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(
                        f"MindGraph {operation_name} 第 {attempt + 1} 次失败 ({e.status}): "
                        f"{str(e)[:100]}, {delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"MindGraph {operation_name} 在 {self.MAX_RETRIES} 次尝试后仍失败: {e}")
            except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(
                        f"MindGraph {operation_name} 第 {attempt + 1} 次连接失败: "
                        f"{str(e)[:100]}, {delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"MindGraph {operation_name} 在 {self.MAX_RETRIES} 次尝试后仍失败: {e}")

        raise last_exception

    # ═══════════════════════════════════════
    # 文档摄入
    # ═══════════════════════════════════════

    def ingest_document(self, text: str, project_id: str, source_name: str = "",
                        layers: Optional[List[str]] = None) -> str:
        """
        异步文档摄入（自动分块、实体/关系提取）

        Returns:
            job_id: 异步任务ID
        """
        result = self._with_retry(
            self._mg.ingest_document,
            content=text,
            agent_id=project_id,
            title=source_name or None,
            layers=layers,
            operation_name=f"文档摄入(project={project_id})",
        )
        job_id = result.get("job_id", "")
        logger.info(f"文档摄入任务已提交: job_id={job_id}, project={project_id}")
        return job_id

    def ingest_chunk(self, text: str, project_id: str,
                     layers: Optional[List[str]] = None,
                     label: Optional[str] = None,
                     chunk_type: Optional[str] = None) -> Dict[str, Any]:
        """
        同步文本块摄入（<8000字符，立即返回提取结果）
        """
        result = self._with_retry(
            self._mg.ingest_chunk,
            content=text,
            agent_id=project_id,
            layers=layers,
            label=label,
            chunk_type=chunk_type,
            operation_name=f"文本块摄入(project={project_id})",
        )
        logger.debug(f"文本块摄入完成: project={project_id}, text_len={len(text)}")
        return result

    def poll_job(self, job_id: str, timeout: int = 600, poll_interval: float = 3.0) -> Dict[str, Any]:
        """轮询异步摄入任务状态"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            result = self._with_retry(
                self._mg.get_job, job_id,
                operation_name=f"轮询任务({job_id[:12]})",
            )
            status = result.get("status", "")

            if status == "completed":
                logger.info(f"摄入任务完成: job_id={job_id}")
                return result
            elif status == "failed":
                error = result.get("error", "未知错误")
                logger.error(f"摄入任务失败: job_id={job_id}, error={error}")
                raise RuntimeError(f"MindGraph摄入任务失败: {error}")
            elif status == "cancelled":
                raise RuntimeError(f"MindGraph摄入任务已取消: {job_id}")

            elapsed = int(time.time() - start_time)
            progress = result.get("progress", {})
            logger.debug(f"摄入任务进行中: job_id={job_id}, status={status}, elapsed={elapsed}s, progress={progress}")
            time.sleep(poll_interval)

        raise TimeoutError(f"等待MindGraph摄入任务超时({timeout}秒): job_id={job_id}")

    def get_job(self, job_id: str) -> Dict[str, Any]:
        """获取任务状态（不轮询）"""
        return self._with_retry(
            self._mg.get_job, job_id,
            operation_name=f"检查任务({job_id[:12]})",
        )

    # ═══════════════════════════════════════
    # 搜索与检索
    # ═══════════════════════════════════════

    def search_hybrid(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """
        混合搜索（BM25 + 语义向量 + RRF融合）

        Returns:
            {"results": [...]}
        """
        # SDK的hybrid_search不支持agent_id，使用retrieve直接传递
        results = self._with_retry(
            self._mg.retrieve,
            action="hybrid",
            query=query,
            limit=limit,
            agent_id=project_id,
            operation_name=f"混合搜索(query={query[:30]}...)",
        )
        # SDK返回list，标准化为dict
        if isinstance(results, list):
            results = {"results": results}
        logger.info(f"搜索完成: 找到 {len(results.get('results', []))} 条结果")
        return results

    def search_text(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """全文搜索（BM25）"""
        results = self._with_retry(
            self._mg.retrieve,
            action="text",
            query=query,
            limit=limit,
            agent_id=project_id,
            operation_name=f"全文搜索(query={query[:30]}...)",
        )
        if isinstance(results, list):
            results = {"results": results}
        return results

    def search_semantic(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """语义搜索 — 降级为hybrid（semantic需要embedding配置）"""
        return self.search_hybrid(query, project_id, limit)

    def retrieve_context(self, query: str, project_id: Optional[str] = None,
                         k: int = 5, depth: int = 1,
                         include_chunks: Optional[bool] = None) -> Dict[str, Any]:
        """
        图谱增强RAG检索

        SDK v0.1.4+ 提供原生 retrieve_context() 方法，搜索整个图谱。
        当 project_id 指定时，通过 _request 传入 agent_id 进行命名空间过滤。
        当 project_id 为 None 时，使用 SDK 原生方法搜索全量图谱。
        """
        if project_id:
            # 带agent_id命名空间过滤
            body: Dict[str, Any] = {
                "query": query,
                "k": k,
                "depth": depth,
                "agent_id": project_id,
            }
            if include_chunks is not None:
                body["include_chunks"] = include_chunks
            return self._with_retry(
                self._mg._request, "POST", "/retrieve/context", body,
                operation_name=f"RAG检索(query={query[:30]}..., k={k})",
            )
        else:
            # 搜索整个图谱（MindGraph连接模式）
            kwargs: Dict[str, Any] = {"query": query, "k": k, "depth": depth}
            if include_chunks is not None:
                kwargs["include_chunks"] = include_chunks
            return self._with_retry(
                self._mg.retrieve_context,
                **kwargs,
                operation_name=f"RAG全局检索(query={query[:30]}..., k={k})",
            )

    # ═══════════════════════════════════════
    # 认知查询
    # ═══════════════════════════════════════

    def get_weak_claims(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        获取低置信度声明

        使用SDK的dedicated GET /claims/weak 端点。
        注意：该端点不支持agent_id过滤，返回全局结果。
        """
        results = self._with_retry(
            self._mg.get_weak_claims,
            operation_name="获取弱声明",
        )
        if isinstance(results, list):
            return {"results": results[:limit]}
        return results

    def get_contradictions(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        获取未解决的矛盾

        使用SDK的dedicated GET /contradictions 端点。
        注意：该端点不支持agent_id过滤，返回全局结果。
        """
        results = self._with_retry(
            self._mg.get_contradictions,
            operation_name="获取矛盾",
        )
        if isinstance(results, list):
            return {"results": results[:limit]}
        return results

    def get_open_questions(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """
        获取开放问题

        使用SDK的dedicated GET /questions 端点。
        注意：该端点不支持agent_id过滤，返回全局结果。
        """
        results = self._with_retry(
            self._mg.get_open_questions,
            operation_name="获取开放问题",
        )
        if isinstance(results, list):
            return {"results": results[:limit]}
        return results

    # ═══════════════════════════════════════
    # 节点/边列表
    # ═══════════════════════════════════════

    def list_nodes(
        self,
        project_id: str,
        node_type: Optional[str] = None,
        layer: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """分页列出节点"""
        result = self._with_retry(
            self._mg.get_nodes,
            node_type=node_type,
            layer=layer,
            limit=limit,
            offset=offset,
            operation_name=f"列出节点(project={project_id})",
        )
        return result.get("items", result) if isinstance(result, dict) else result

    def list_all_graph_nodes(self, node_type: Optional[str] = None,
                            layer: Optional[str] = None, max_items: int = 2000) -> List[Dict[str, Any]]:
        """
        获取整个图谱的所有节点（不按agent_id过滤）

        用于连接已有MindGraph图谱的场景，读取用户通过MindGraph Cloud
        构建的全量图谱数据。
        """
        all_nodes = []
        offset = 0
        page_size = 100

        while len(all_nodes) < max_items:
            batch = self.list_nodes(
                project_id="__global__",  # 仅用于日志，不影响查询
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

        logger.info(f"全量图谱节点读取完成: 共 {len(all_nodes)} 个节点")
        return all_nodes[:max_items]

    def list_all_graph_edges(self, nodes: Optional[List[Dict[str, Any]]] = None,
                            max_items: int = 5000) -> List[Dict[str, Any]]:
        """
        获取图谱的边（不按agent_id过滤）

        使用 POST /edges/batch 批量接口，一次请求获取所有节点间的边。

        Args:
            nodes: 预先获取的节点列表（避免重复查询）
            max_items: 最多返回多少条边
        """
        if nodes is None:
            nodes = self.list_all_graph_nodes()

        node_uids = [n.get("uid", "") for n in nodes if n.get("uid")]
        if not node_uids:
            return []

        logger.info(f"批量查询 {len(node_uids)} 个节点间的边...")

        try:
            edges = self._with_retry(
                self._mg.get_edges_batch, node_uids,
                operation_name=f"批量查询边({len(node_uids)}个节点)",
            )
            logger.info(f"全量图谱边读取完成: 共 {len(edges)} 条边")
            return edges[:max_items]
        except Exception as e:
            logger.warning(f"批量边查询失败，回退到逐节点查询: {e}")
            # 回退：逐节点查询（限制数量避免超时）
            all_edges = []
            seen_uids = set()
            for node_uid in node_uids[:200]:
                try:
                    node_edges = self._with_retry(
                        self._mg.get_edges, from_uid=node_uid,
                        operation_name=f"列出边(from={node_uid[:12]})",
                    )
                    for edge in node_edges:
                        edge_uid = edge.get("uid", "")
                        if edge_uid and edge_uid not in seen_uids:
                            seen_uids.add(edge_uid)
                            all_edges.append(edge)
                except Exception:
                    pass
            logger.info(f"回退边查询完成: 共 {len(all_edges)} 条边")
            return all_edges[:max_items]

    def list_all_nodes(self, project_id: str, node_type: Optional[str] = None,
                       layer: Optional[str] = None, max_items: int = 2000) -> List[Dict[str, Any]]:
        """
        获取项目的所有节点

        优先使用 get_agent_nodes(agent_id) 进行命名空间过滤。
        如果该端点不可用，回退到全量分页查询。
        """
        try:
            nodes = self._with_retry(
                self._mg.get_agent_nodes, project_id,
                operation_name=f"获取Agent节点(project={project_id})",
            )
            # 按node_type过滤
            if node_type:
                nodes = [n for n in nodes if n.get("node_type") == node_type]
            return nodes[:max_items]
        except Exception as e:
            logger.debug(f"get_agent_nodes失败，回退到分页查询: {e}")

        # 回退：全量分页
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
        获取项目所有边

        使用 POST /edges/batch 批量接口获取节点间的边。
        回退到逐节点查询（如果批量接口不可用）。
        """
        nodes = self.list_all_nodes(project_id=project_id)
        node_uids = [n.get("uid", "") for n in nodes if n.get("uid")]
        if not node_uids:
            return []

        try:
            edges = self._with_retry(
                self._mg.get_edges_batch, node_uids,
                operation_name=f"批量查询边({len(node_uids)}个节点, project={project_id})",
            )
            return edges
        except Exception as e:
            logger.debug(f"批量边查询失败，回退到逐节点查询: {e}")

        # 回退：逐节点查询
        all_edges = []
        seen_uids = set()

        for node in nodes:
            node_uid = node.get("uid", "")
            if not node_uid:
                continue
            try:
                edges = self._with_retry(
                    self._mg.get_edges, from_uid=node_uid,
                    operation_name=f"列出边(from={node_uid[:12]})",
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
    # 单节点操作
    # ═══════════════════════════════════════

    def get_node(self, uid: str) -> Dict[str, Any]:
        """获取单个节点详情"""
        return self._with_retry(
            self._mg.get_node, uid,
            operation_name=f"获取节点({uid[:12]})",
        )

    def get_nodes_batch(self, uids: List[str]) -> List[Dict[str, Any]]:
        """批量获取节点详情（单次API调用）"""
        if not uids:
            return []
        return self._with_retry(
            self._mg.get_nodes_batch, uids,
            operation_name=f"批量获取节点({len(uids)}个)",
        )

    def get_edges_batch(self, node_uids: List[str]) -> List[Dict[str, Any]]:
        """批量获取节点间的边（单次API调用）"""
        if not node_uids:
            return []
        return self._with_retry(
            self._mg.get_edges_batch, node_uids,
            operation_name=f"批量查询边({len(node_uids)}个节点)",
        )

    def get_neighborhood(self, uid: str, depth: int = 1) -> Dict[str, Any]:
        """
        获取节点的邻居（BFS）

        SDK的neighborhood()返回traverse步骤列表（节点信息）。
        边数据通过get_edges(from_uid)单独获取，合并为
        {"nodes": [...], "edges": [...]} 格式。
        """
        result = self._with_retry(
            self._mg.neighborhood, uid, max_depth=depth,
            operation_name=f"获取邻居({uid[:12]}, depth={depth})",
        )
        nodes = result if isinstance(result, list) else result.get("nodes", [])

        # 获取该节点的出边和入边
        edges = []
        try:
            out_edges = self._with_retry(
                self._mg.get_edges, from_uid=uid,
                operation_name=f"获取出边({uid[:12]})",
            )
            edges.extend(out_edges)
        except Exception:
            pass
        try:
            in_edges = self._with_retry(
                self._mg.get_edges, to_uid=uid,
                operation_name=f"获取入边({uid[:12]})",
            )
            # 去重（如果有自环边）
            seen = {e.get("uid") for e in edges}
            for e in in_edges:
                if e.get("uid") not in seen:
                    edges.append(e)
        except Exception:
            pass

        return {"nodes": nodes, "edges": edges}

    def get_node_history(self, uid: str) -> List[Dict[str, Any]]:
        """获取节点版本历史"""
        return self._with_retry(
            self._mg.get_node_history, uid,
            operation_name=f"节点历史({uid[:12]})",
        )

    # ═══════════════════════════════════════
    # 图遍历
    # ═══════════════════════════════════════

    def traverse_chain(self, start_uid: str, max_depth: int = 5) -> Dict[str, Any]:
        """推理链遍历"""
        result = self._with_retry(
            self._mg.reasoning_chain, start_uid, max_depth=max_depth,
            operation_name=f"推理链({start_uid[:12]})",
        )
        if isinstance(result, list):
            return {"chain": result}
        return result

    # ═══════════════════════════════════════
    # 实体管理
    # ═══════════════════════════════════════

    def create_entity(self, name: str, entity_type: str, project_id: str,
                      description: str = "", props: Optional[Dict] = None) -> Dict[str, Any]:
        """显式创建实体节点"""
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
            operation_name=f"创建实体({name})",
        )

    def resolve_entity(self, name: str, project_id: str) -> Dict[str, Any]:
        """精确解析实体名称"""
        return self._with_retry(
            self._mg.resolve_entity, name, agent_id=project_id,
            operation_name=f"解析实体({name})",
        )

    def fuzzy_resolve_entity(self, name: str, project_id: str, limit: int = 5) -> Dict[str, Any]:
        """模糊解析实体名称"""
        return self._with_retry(
            self._mg.fuzzy_resolve_entity, name, limit=limit, agent_id=project_id,
            operation_name=f"模糊解析({name})",
        )

    # ═══════════════════════════════════════
    # 边创建 + Agent注册
    # ═══════════════════════════════════════

    def add_link(self, from_uid: str, to_uid: str, edge_type: str,
                 project_id: Optional[str] = None,
                 agent_id: Optional[str] = None) -> Any:
        """创建通用边（轻量级）"""
        ns = project_id or agent_id  # 兼容旧调用方式
        return self._with_retry(
            self._mg.add_link,
            from_uid=from_uid, to_uid=to_uid, edge_type=edge_type,
            agent_id=ns,
            operation_name=f"创建边({edge_type})",
        )

    def add_edge(self, from_uid: str, to_uid: str, edge_type: str,
                 props: Optional[Dict] = None,
                 project_id: Optional[str] = None,
                 agent_id: Optional[str] = None) -> Any:
        """创建带属性的边（SDK自动注入props._type）"""
        ns = project_id or agent_id  # 兼容旧调用方式
        return self._with_retry(
            self._mg.add_edge,
            from_uid=from_uid, to_uid=to_uid, edge_type=edge_type,
            props=props,
            agent_id=ns,
            operation_name=f"创建边({edge_type})",
        )

    def register_agent_node(self, name: str, project_id: str,
                            summary: str = "",
                            props: Optional[Dict] = None) -> Dict[str, Any]:
        """
        创建Agent节点（SDK自动注入props._type）

        Args:
            name: Agent名称
            project_id: 项目ID（命名空间隔离）
            summary: Agent简介
            props: 额外属性（stance, role, influence_weight等）
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
            operation_name=f"注册Agent({name})",
        )

    # ═══════════════════════════════════════
    # Agent发言摄入
    # ═══════════════════════════════════════

    def ingest_agent_post(self, agent_name: str, content: str, project_id: str,
                          platform: str = "", round_num: int = 0) -> Dict[str, Any]:
        """
        摄入Agent发言 — 让MindGraph自动决定认知类型

        返回值包含 extracted_node_uids 用于创建AUTHORED边。
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
        """添加结构化声明"""
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
            operation_name=f"添加声明({agent_name or 'unknown'}, confidence={confidence:.2f})",
        )

    # ═══════════════════════════════════════
    # 记忆层 - 会话管理
    # ═══════════════════════════════════════

    def open_session(self, project_id: str, session_name: str) -> str:
        """打开模拟会话"""
        result = self._with_retry(
            self._mg.session,
            action="open",
            label=session_name,
            props={"focus_summary": session_name},
            agent_id=project_id,
            operation_name="打开会话",
        )
        return result.get("uid", "")

    def trace_session(self, session_uid: str, content: str, project_id: str,
                      trace_type: str = "observation") -> Dict[str, Any]:
        """添加会话跟踪条目"""
        return self._with_retry(
            self._mg.session,
            action="trace",
            session_uid=session_uid,
            label=content[:100],
            props={"content": content, "trace_type": trace_type},
            agent_id=project_id,
            operation_name="会话跟踪",
        )

    def close_session(self, session_uid: str, project_id: str) -> Dict[str, Any]:
        """关闭模拟会话"""
        return self._with_retry(
            self._mg.session,
            action="close",
            session_uid=session_uid,
            agent_id=project_id,
            operation_name="关闭会话",
        )

    def distill(self, label: str, source_uids: List[str], project_id: str,
                content: str = "") -> Dict[str, Any]:
        """蒸馏摘要"""
        return self._with_retry(
            self._mg.distill,
            label=label,
            summarizes_uids=source_uids,
            props={"content": content},
            agent_id=project_id,
            operation_name="蒸馏摘要",
        )

    # ═══════════════════════════════════════
    # 认识论层 - 假说与异常
    # ═══════════════════════════════════════

    def add_hypothesis(self, statement: str, project_id: str,
                       confidence: float = 0.5) -> Dict[str, Any]:
        """注册可验证假说"""
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
            operation_name=f"注册假说(project={project_id})",
        )

    def record_anomaly(self, description: str, project_id: str,
                       severity: str = "medium",
                       agent_name: Optional[str] = None) -> Dict[str, Any]:
        """记录行为异常"""
        label = description[:100]
        if agent_name:
            label = f"[异常] {agent_name}: {description[:80]}"
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
            operation_name=f"记录异常({severity})",
        )

    # ═══════════════════════════════════════
    # 认识论层 - 模式识别
    # ═══════════════════════════════════════

    def record_pattern(self, name: str, description: str, project_id: str,
                       instance_count: int = 1) -> Dict[str, Any]:
        """记录涌现模式"""
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
            operation_name=f"记录模式({name})",
        )

    # ═══════════════════════════════════════
    # 意图层 - 目标与决策
    # ═══════════════════════════════════════

    def create_goal(self, label: str, project_id: str,
                    description: str = "", priority: str = "medium",
                    goal_type: str = "social") -> Dict[str, Any]:
        """注册Agent目标"""
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
            operation_name=f"创建目标({label[:30]})",
        )

    def record_decision(self, agent_name: str, description: str,
                        chosen_option: str, rationale: str,
                        project_id: str) -> Dict[str, Any]:
        """
        记录Agent的可观察决策

        使用SDK的3步便捷方法：open_decision → add_option → resolve_decision
        """
        # Step 1: 打开决策
        decision = self._with_retry(
            self._mg.open_decision,
            label=description[:100],
            props={"description": description},
            agent_id=project_id,
            operation_name=f"打开决策({agent_name})",
        )

        decision_uid = decision.get("uid", "")

        if decision_uid:
            # Step 2: 添加已选选项
            option_uid = ""
            try:
                option_result = self._with_retry(
                    self._mg.add_option,
                    decision_uid=decision_uid,
                    label=chosen_option[:100],
                    props={"description": chosen_option},
                    agent_id=project_id,
                    operation_name=f"添加选项({agent_name})",
                )
                option_uid = option_result.get("uid", "")
            except Exception as e:
                logger.warning(f"添加决策选项失败: {e}")

            # Step 3: 解决决策
            if option_uid:
                try:
                    self._with_retry(
                        self._mg.resolve_decision,
                        decision_uid=decision_uid,
                        chosen_option_uid=option_uid,
                        summary=rationale,
                        agent_id=project_id,
                        operation_name=f"解决决策({agent_name})",
                    )
                except Exception as e:
                    logger.warning(f"解决决策失败: {e}")

        return decision

    # ═══════════════════════════════════════
    # 记忆层 - Journal
    # ═══════════════════════════════════════

    def create_journal(self, content: str, project_id: str,
                       journal_type: str = "stance", tags: Optional[List[str]] = None,
                       session_uid: Optional[str] = None) -> Dict[str, Any]:
        """创建Journal记忆条目（Memory层）"""
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
            operation_name=f"创建Journal({journal_type})",
        )

    # ═══════════════════════════════════════
    # 现实层 - 观察记录
    # ═══════════════════════════════════════

    def capture_observation(self, content: str, project_id: str,
                            observation_type: str = "simulation_event",
                            timestamp: Optional[str] = None) -> Dict[str, Any]:
        """
        记录事实观察（Reality层）

        Note: Observations do NOT accept session_uid — they belong to the
        Reality layer, not Memory layer. Use Journal nodes for session-linked entries.
        """
        props = {
            "content": content,
            "observation_type": observation_type,
        }
        if timestamp:
            props["timestamp"] = timestamp
        return self._with_retry(
            self._mg.capture,
            action="observation",
            label=content[:100],
            props=props,
            agent_id=project_id,
            operation_name=f"记录观察({observation_type})",
        )

    # ═══════════════════════════════════════
    # 生命周期管理
    # ═══════════════════════════════════════

    def delete_node(self, uid: str) -> Any:
        """软删除节点"""
        return self._with_retry(
            self._mg.delete_node, uid,
            operation_name=f"删除节点({uid[:12]})",
        )

    def decay_salience(self, project_id: str, half_life_secs: int = 86400,
                       min_salience: float = 0.1) -> Dict[str, Any]:
        """
        批量衰减显著度

        警告：SDK的 decay() 是全局操作，会影响所有项目的节点，
        不仅限于 project_id 指定的项目。project_id 仅用于日志记录。
        """
        logger.warning(
            f"decay_salience 是全局操作，将影响所有项目节点 "
            f"(调用方: project={project_id})"
        )
        result = self._with_retry(
            self._mg.decay,
            half_life_secs=half_life_secs,
            min_salience=min_salience,
            operation_name=f"批量衰减(caller={project_id})",
        )
        logger.info(f"批量衰减完成: caller={project_id}, result={result}")
        return result if isinstance(result, dict) else {"result": result}

    def delete_project_data(self, project_id: str):
        """
        删除项目的所有数据

        使用list_all_nodes()（优先get_agent_nodes）确保只删除
        属于该项目命名空间的节点，而非全局节点。
        """
        logger.info(f"开始删除项目数据: project_id={project_id}")
        deleted = 0
        max_iterations = 100  # 安全限制，防止无限循环

        for iteration in range(max_iterations):
            nodes = self.list_all_nodes(project_id=project_id, max_items=100)
            if not nodes:
                break
            for node in nodes:
                uid = node.get("uid", "")
                if uid:
                    try:
                        self.delete_node(uid)
                        deleted += 1
                    except Exception as e:
                        logger.warning(f"删除节点失败: uid={uid}, error={e}")
        else:
            logger.warning(
                f"delete_project_data 达到迭代上限({max_iterations}): "
                f"project_id={project_id}, deleted={deleted}"
            )

        logger.info(f"项目数据删除完成: project_id={project_id}, deleted={deleted}")

    # ═══════════════════════════════════════
    # 统计与导出
    # ═══════════════════════════════════════

    def get_graph_statistics(self, project_id: str) -> Dict[str, Any]:
        """获取图谱统计信息"""
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
