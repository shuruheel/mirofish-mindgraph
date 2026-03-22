"""
MindGraph客户端封装
统一的MindGraph REST API访问层，带项目级命名空间隔离

MindGraph没有per-graph隔离（一个API Key = 一个组织级图谱），
因此通过agent_id实现项目级命名空间：
- 写入时：所有请求携带 agent_id=project_id
- 读取时：通过 agent=project_id 参数过滤
"""

import time
import requests
from typing import Any, Dict, List, Optional
from ..config import Config
from .logger import get_logger

logger = get_logger('mirofish.mindgraph_client')


class MindGraphClient:
    """
    MindGraph REST API 客户端

    核心职责：
    1. HTTP请求封装（带重试机制）
    2. agent_id命名空间隔离
    3. 响应格式标准化
    """

    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # 秒，指数退避

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or Config.MINDGRAPH_API_KEY
        self.base_url = (base_url or Config.MINDGRAPH_BASE_URL).rstrip('/')
        if not self.api_key:
            raise ValueError("MINDGRAPH_API_KEY 未配置")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        logger.info(f"MindGraphClient 初始化完成: base_url={self.base_url}")

    # ═══════════════════════════════════════
    # HTTP基础层
    # ═══════════════════════════════════════

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict] = None,
        params: Optional[Dict] = None,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        operation_name: str = "API call"
    ) -> Dict[str, Any]:
        """带指数退避重试的HTTP请求"""
        max_retries = max_retries or self.MAX_RETRIES
        delay = retry_delay or self.RETRY_DELAY
        url = f"{self.base_url}{path}"
        last_exception = None

        for attempt in range(max_retries):
            try:
                resp = requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=json_body,
                    params=params,
                    timeout=60
                )
                resp.raise_for_status()
                if resp.status_code == 204:
                    return {}
                return resp.json()
            except requests.exceptions.HTTPError as e:
                last_exception = e
                status = e.response.status_code if e.response is not None else 0
                # 不重试客户端错误(4xx)，除了429
                if 400 <= status < 500 and status != 429:
                    logger.error(f"MindGraph {operation_name} 客户端错误 ({status}): {e}")
                    raise
                if attempt < max_retries - 1:
                    logger.warning(
                        f"MindGraph {operation_name} 第 {attempt + 1} 次失败 ({status}): "
                        f"{str(e)[:100]}, {delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"MindGraph {operation_name} 在 {max_retries} 次尝试后仍失败: {e}")
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"MindGraph {operation_name} 第 {attempt + 1} 次连接失败: "
                        f"{str(e)[:100]}, {delay:.1f}秒后重试..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"MindGraph {operation_name} 在 {max_retries} 次尝试后仍失败: {e}")

        raise last_exception

    # ═══════════════════════════════════════
    # 文档摄入 (替代 Zep graph.add_batch / graph.add)
    # ═══════════════════════════════════════

    def ingest_document(self, text: str, project_id: str, source_name: str = "",
                        layers: Optional[List[str]] = None) -> str:
        """
        异步文档摄入（自动分块、实体/关系提取）
        替代 Zep client.graph.add_batch()

        Args:
            text: 文档文本
            project_id: 项目ID（用作agent_id命名空间）
            source_name: 来源名称
            layers: 提取层列表，如 ["reality", "epistemic"]。
                    默认None表示使用MindGraph默认值（reality+epistemic）

        Returns:
            job_id: 异步任务ID，用于轮询状态
        """
        body = {
            "content": text,
            "agent_id": project_id,
        }
        if source_name:
            body["source_name"] = source_name
        if layers:
            body["layers"] = layers

        result = self._request(
            "POST", "/ingest/document",
            json_body=body,
            operation_name=f"文档摄入(project={project_id})"
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
        替代 Zep client.graph.add(type="text", data=...)

        Args:
            text: 文本块（<8000字符）
            project_id: 项目ID
            layers: 提取层列表，如 ["reality", "epistemic", "memory"]。
                    默认None表示使用MindGraph默认值（reality+epistemic+memory）
            label: 块节点的可读标签（用于标识来源Agent等）
            chunk_type: 块类型分类（如 "agent_post", "seed_document"）

        Returns:
            提取结果（包含创建的节点和边信息）
        """
        body = {
            "content": text,
            "agent_id": project_id,
        }
        if layers:
            body["layers"] = layers
        if label:
            body["label"] = label
        if chunk_type:
            body["chunk_type"] = chunk_type

        result = self._request(
            "POST", "/ingest/chunk",
            json_body=body,
            operation_name=f"文本块摄入(project={project_id})"
        )
        logger.debug(f"文本块摄入完成: project={project_id}, text_len={len(text)}")
        return result

    def poll_job(self, job_id: str, timeout: int = 600, poll_interval: float = 3.0) -> Dict[str, Any]:
        """
        轮询异步摄入任务状态
        替代 Zep client.graph.episode.get() 轮询

        Args:
            job_id: 任务ID
            timeout: 超时秒数
            poll_interval: 轮询间隔秒数

        Returns:
            任务最终状态
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            result = self._request(
                "GET", f"/jobs/{job_id}",
                operation_name=f"轮询任务({job_id[:12]})"
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

            # 还在处理中
            elapsed = int(time.time() - start_time)
            progress = result.get("progress", {})
            logger.debug(f"摄入任务进行中: job_id={job_id}, status={status}, elapsed={elapsed}s, progress={progress}")
            time.sleep(poll_interval)

        raise TimeoutError(f"等待MindGraph摄入任务超时({timeout}秒): job_id={job_id}")

    # ═══════════════════════════════════════
    # 搜索与检索 (替代 Zep graph.search)
    # ═══════════════════════════════════════

    def _normalize_search_result(self, result) -> Dict[str, Any]:
        """Normalize search results — API returns a list, wrap it for consistent access."""
        if isinstance(result, list):
            return {"results": result}
        return result

    def search_hybrid(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """
        混合搜索（BM25 + 语义向量 + RRF融合）
        替代 Zep client.graph.search(reranker="rrf")

        Args:
            query: 搜索查询
            project_id: 项目ID
            limit: 返回结果数

        Returns:
            {"results": [...]} — normalized
        """
        body = {
            "action": "hybrid",
            "query": query,
            "limit": limit,
            "agent_id": project_id,
        }

        result = self._request(
            "POST", "/retrieve",
            json_body=body,
            operation_name=f"混合搜索(query={query[:30]}...)"
        )
        result = self._normalize_search_result(result)
        logger.info(f"搜索完成: 找到 {len(result.get('results', []))} 条结果")
        return result

    def search_text(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """全文搜索（BM25）"""
        body = {
            "action": "text",
            "query": query,
            "limit": limit,
            "agent_id": project_id,
        }
        result = self._request(
            "POST", "/retrieve",
            json_body=body,
            operation_name=f"全文搜索(query={query[:30]}...)"
        )
        return self._normalize_search_result(result)

    def search_semantic(self, query: str, project_id: str, limit: int = 10) -> Dict[str, Any]:
        """语义搜索 — 降级为hybrid（semantic action未在当前API版本实现）"""
        return self.search_hybrid(query, project_id, limit)

    def retrieve_context(self, query: str, project_id: str,
                         k: int = 5, depth: int = 1) -> Dict[str, Any]:
        """
        图谱增强RAG检索 — 返回语义匹配的文本块 + 关联的图谱节点

        MindGraph特有端点，比纯搜索多一步图谱扩展：
        1. 语义匹配文本块
        2. 从匹配块出发，沿图谱边扩展depth跳
        3. 返回chunks + 相关graph nodes

        Args:
            query: 查询文本
            project_id: 项目ID
            k: 返回的文本块数量
            depth: 图谱扩展深度（跳数）
        """
        body = {
            "query": query,
            "k": k,
            "depth": depth,
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/retrieve/context",
            json_body=body,
            operation_name=f"RAG检索(query={query[:30]}..., k={k}, depth={depth})"
        )

    # ═══════════════════════════════════════
    # 认知查询 (MindGraph特有，Zep无等价功能)
    # ═══════════════════════════════════════

    def get_weak_claims(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """获取低置信度声明"""
        body = {
            "action": "weak_claims",
            "limit": limit,
            "agent_id": project_id,
        }
        result = self._request("POST", "/retrieve", json_body=body, operation_name="获取弱声明")
        return self._normalize_search_result(result)

    def get_contradictions(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """获取未解决的矛盾"""
        body = {
            "action": "unresolved_contradictions",
            "limit": limit,
            "agent_id": project_id,
        }
        result = self._request("POST", "/retrieve", json_body=body, operation_name="获取矛盾")
        return self._normalize_search_result(result)

    def get_open_questions(self, project_id: str, limit: int = 20) -> Dict[str, Any]:
        """获取开放问题"""
        body = {
            "action": "open_questions",
            "limit": limit,
            "agent_id": project_id,
        }
        result = self._request("POST", "/retrieve", json_body=body, operation_name="获取开放问题")
        return self._normalize_search_result(result)

    # ═══════════════════════════════════════
    # 节点/边列表 (替代 Zep fetch_all_nodes / fetch_all_edges)
    # ═══════════════════════════════════════

    def list_nodes(
        self,
        project_id: str,
        node_type: Optional[str] = None,
        layer: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        分页列出节点
        替代 Zep fetch_all_nodes + client.graph.node.get_by_graph_id()
        """
        params = {
            "agent": project_id,
            "limit": limit,
            "offset": offset,
        }
        if node_type:
            params["type"] = node_type
        if layer:
            params["layer"] = layer

        result = self._request(
            "GET", "/nodes",
            params=params,
            operation_name=f"列出节点(project={project_id})"
        )
        return result.get("items", result) if isinstance(result, dict) else result

    def list_all_nodes(self, project_id: str, node_type: Optional[str] = None,
                       layer: Optional[str] = None, max_items: int = 2000) -> List[Dict[str, Any]]:
        """自动分页获取所有节点"""
        all_nodes = []
        offset = 0
        page_size = 100

        while len(all_nodes) < max_items:
            batch = self.list_nodes(
                project_id=project_id,
                node_type=node_type,
                layer=layer,
                limit=page_size,
                offset=offset
            )
            if not batch:
                break
            all_nodes.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size

        return all_nodes[:max_items]

    def list_edges(
        self,
        project_id: str,
        from_uid: Optional[str] = None,
        to_uid: Optional[str] = None,
        edge_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        列出边 — GET /edges 需要 from_uid 或 to_uid 参数

        MindGraph API要求至少指定from_uid或to_uid之一。
        如果都未指定，返回空列表（通过list_all_edges间接查询）。
        """
        if not from_uid and not to_uid:
            return []

        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if from_uid:
            params["from_uid"] = from_uid
        if to_uid:
            params["to_uid"] = to_uid
        if edge_type:
            params["edge_type"] = edge_type

        result = self._request(
            "GET", "/edges",
            params=params,
            operation_name=f"列出边(project={project_id})"
        )
        return result.get("items", result) if isinstance(result, dict) else result

    def list_all_edges(self, project_id: str) -> List[Dict[str, Any]]:
        """
        获取项目所有边 — 通过遍历项目节点的出边实现

        MindGraph的 GET /edges 要求 from_uid 或 to_uid，无法按agent过滤。
        因此先列出所有节点，再查询每个节点的出边并去重。
        """
        nodes = self.list_all_nodes(project_id=project_id)
        all_edges = []
        seen_uids = set()

        for node in nodes:
            node_uid = node.get("uid", "")
            if not node_uid:
                continue
            try:
                edges = self.list_edges(
                    project_id=project_id,
                    from_uid=node_uid,
                    limit=100,
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
    # 单节点操作 (替代 Zep graph.node.get / get_entity_edges)
    # ═══════════════════════════════════════

    def get_node(self, uid: str) -> Dict[str, Any]:
        """
        获取单个节点详情
        替代 Zep client.graph.node.get(uuid_=uid)
        """
        return self._request("GET", f"/node/{uid}", operation_name=f"获取节点({uid[:12]})")

    def get_neighborhood(self, uid: str, depth: int = 1) -> Dict[str, Any]:
        """
        获取节点的邻居（BFS）
        替代 Zep client.graph.node.get_entity_edges(node_uuid)

        API返回列表，每项含 node_uid, label, node_type, edge_type, depth, parent_uid。
        标准化为 {"nodes": [...], "edges": []} 格式。
        """
        result = self._request(
            "GET", f"/neighborhood/{uid}",
            params={"depth": depth},
            operation_name=f"获取邻居({uid[:12]}, depth={depth})"
        )
        if isinstance(result, list):
            return {"nodes": result, "edges": []}
        return result

    def get_subgraph(self, node_uids: List[str]) -> Dict[str, Any]:
        """提取子图"""
        body = {"node_uids": node_uids}
        return self._request("POST", "/subgraph", json_body=body, operation_name="提取子图")

    def get_node_history(self, uid: str) -> List[Dict[str, Any]]:
        """获取节点版本历史"""
        result = self._request("GET", f"/node/{uid}/history", operation_name=f"节点历史({uid[:12]})")
        return result if isinstance(result, list) else result.get("items", [])

    # ═══════════════════════════════════════
    # 图遍历 (替代 Zep 的全图读取模式)
    # ═══════════════════════════════════════

    def traverse_chain(self, start_uid: str, max_depth: int = 5) -> Dict[str, Any]:
        """推理链遍历 — API返回列表，标准化为 {"chain": [...]}"""
        result = self._request(
            "GET", f"/chain/{start_uid}",
            params={"max_depth": max_depth},
            operation_name=f"推理链({start_uid[:12]})"
        )
        if isinstance(result, list):
            return {"chain": result}
        return result

    def traverse_path(self, from_uid: str, to_uid: str, max_depth: int = 5) -> Dict[str, Any]:
        """最短路径"""
        return self._request(
            "GET", "/path",
            params={"from": from_uid, "to": to_uid, "max_depth": max_depth},
            operation_name=f"最短路径({from_uid[:12]}→{to_uid[:12]})"
        )

    # ═══════════════════════════════════════
    # 实体管理 (替代 Zep 的本体设置)
    # ═══════════════════════════════════════

    def create_entity(self, name: str, entity_type: str, project_id: str,
                      description: str = "", props: Optional[Dict] = None) -> Dict[str, Any]:
        """
        显式创建实体节点
        替代 Zep 的 set_ontology + add_batch 自动提取
        """
        body = {
            "action": "create",
            "label": name,
            "props": {
                "entity_type": entity_type,
                "description": description,
                **(props or {})
            },
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/reality/entity",
            json_body=body,
            operation_name=f"创建实体({name})"
        )

    def relate_entities(self, source_uid: str, target_uid: str, edge_type: str,
                        project_id: Optional[str] = None,
                        props: Optional[Dict] = None) -> Dict[str, Any]:
        """创建实体间关系"""
        body = {
            "action": "relate",
            "source_uid": source_uid,
            "target_uid": target_uid,
            "edge_type": edge_type,
            "props": props or {},
        }
        if project_id:
            body["agent_id"] = project_id
        return self._request(
            "POST", "/reality/entity",
            json_body=body,
            operation_name=f"关联实体({edge_type})"
        )

    def resolve_entity(self, name: str, project_id: str) -> Dict[str, Any]:
        """精确解析实体名称 — API requires 'text' field"""
        body = {
            "action": "resolve",
            "text": name,
            "agent_id": project_id,
        }
        return self._request("POST", "/reality/entity", json_body=body, operation_name=f"解析实体({name})")

    def fuzzy_resolve_entity(self, name: str, project_id: str, limit: int = 5) -> Dict[str, Any]:
        """模糊解析实体名称 — API requires 'text' field"""
        body = {
            "action": "fuzzy_resolve",
            "text": name,
            "limit": limit,
            "agent_id": project_id,
        }
        return self._request("POST", "/reality/entity", json_body=body, operation_name=f"模糊解析({name})")

    # ═══════════════════════════════════════
    # 通用边创建 + Agent注册
    # ═══════════════════════════════════════

    def add_link(self, from_uid: str, to_uid: str, edge_type: str,
                 agent_id: Optional[str] = None) -> Any:
        """
        创建通用边（轻量级，无props）

        用于将Agent节点连接到其创作/决策/异常等节点。
        """
        body: Dict[str, Any] = {
            "from_uid": from_uid,
            "to_uid": to_uid,
            "edge_type": edge_type,
        }
        if agent_id:
            body["agent_id"] = agent_id
        return self._request(
            "POST", "/link",
            json_body=body,
            operation_name=f"创建边({edge_type})"
        )

    def add_edge(self, from_uid: str, to_uid: str, edge_type: str,
                 props: Optional[Dict] = None,
                 agent_id: Optional[str] = None) -> Any:
        """
        创建带属性的边

        比add_link更丰富，支持props字典。
        POST /edge 要求 props 包含 _type 字段。
        """
        edge_props = {"_type": edge_type}
        if props:
            edge_props.update(props)

        body: Dict[str, Any] = {
            "from_uid": from_uid,
            "to_uid": to_uid,
            "edge_type": edge_type,
            "props": edge_props,
        }
        if agent_id:
            body["agent_id"] = agent_id
        return self._request(
            "POST", "/edge",
            json_body=body,
            operation_name=f"创建边({edge_type})"
        )

    def register_agent_node(self, name: str, project_id: str,
                            summary: str = "",
                            props: Optional[Dict] = None) -> Dict[str, Any]:
        """
        创建Agent节点 — 使用 POST /node with node_type=Agent

        为模拟中的每个Agent创建一个持久化节点，后续通过AUTHORED等边
        将其发言、决策、异常等节点连接到此Agent节点。

        POST /node 要求 props 包含 _type 字段。

        Args:
            name: Agent名称
            project_id: 项目ID（命名空间隔离）
            summary: Agent简介
            props: 额外属性（stance, role, influence_weight等）
        """
        node_props = {"_type": "Agent"}
        if props:
            node_props.update(props)

        body: Dict[str, Any] = {
            "label": name,
            "node_type": "Agent",
            "props": node_props,
            "agent_id": project_id,
        }
        if summary:
            body["summary"] = summary
        return self._request(
            "POST", "/node",
            json_body=body,
            operation_name=f"注册Agent({name})"
        )

    # ═══════════════════════════════════════
    # 认识论层 — Agent发言摄入 + 显式声明
    # ═══════════════════════════════════════

    def ingest_agent_post(self, agent_name: str, content: str, project_id: str,
                          platform: str = "", round_num: int = 0) -> Dict[str, Any]:
        """
        摄入Agent发言 — 让MindGraph自动决定认知类型

        不手动强制为Claim。MindGraph的提取LLM会根据内容自动判定节点类型：
        - 观点/判断 → Claim
        - 提问 → Question
        - 事实报告 → Observation
        - 因果预测 → Claim(causal) 或 Hypothesis

        返回值包含 extracted_node_uids — 调用方可用这些UID创建
        Agent→提取节点的AUTHORED边。

        Args:
            agent_name: Agent名称（包含在文本中帮助归因）
            content: 发言内容
            project_id: 项目ID
            platform: 平台（twitter/reddit）
            round_num: 轮次号
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
        """
        添加结构化声明（带可选证据和声明者信息）

        用于将模拟Agent的发言转化为结构化的认识论声明。
        confidence基于Agent的influence_weight得出。

        Args:
            text: 声明内容
            project_id: 项目ID（命名空间）
            confidence: 置信度 (0.0-1.0)
            evidence_text: 支持证据文本（可选）
            agent_name: 做出声明的Agent名称（可选）
        """
        claim_label = text[:100]
        if agent_name:
            claim_label = f"{agent_name}: {text[:80]}"

        body = {
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

        return self._request(
            "POST", "/epistemic/argument",
            json_body=body,
            operation_name=f"添加声明({agent_name or 'unknown'}, confidence={confidence:.2f})"
        )

    # ═══════════════════════════════════════
    # 记忆层 - 会话管理 (MindGraph特有)
    # ═══════════════════════════════════════

    def open_session(self, project_id: str, session_name: str) -> str:
        """打开模拟会话"""
        body = {
            "action": "open",
            "label": session_name,
            "props": {"focus_summary": session_name},
            "agent_id": project_id,
        }
        result = self._request("POST", "/memory/session", json_body=body, operation_name="打开会话")
        return result.get("uid", "")

    def trace_session(self, session_uid: str, content: str, project_id: str,
                      trace_type: str = "observation") -> Dict[str, Any]:
        """添加会话跟踪条目"""
        body = {
            "action": "trace",
            "session_uid": session_uid,
            "label": content[:100],
            "props": {"content": content, "trace_type": trace_type},
            "agent_id": project_id,
        }
        return self._request("POST", "/memory/session", json_body=body, operation_name="会话跟踪")

    def close_session(self, session_uid: str, project_id: str) -> Dict[str, Any]:
        """关闭模拟会话"""
        body = {
            "action": "close",
            "session_uid": session_uid,
            "agent_id": project_id,
        }
        return self._request("POST", "/memory/session", json_body=body, operation_name="关闭会话")

    def distill(self, label: str, source_uids: List[str], project_id: str,
                content: str = "") -> Dict[str, Any]:
        """蒸馏摘要"""
        body = {
            "label": label,
            "summarizes_uids": source_uids,
            "props": {"content": content},
            "agent_id": project_id,
        }
        return self._request("POST", "/memory/distill", json_body=body, operation_name="蒸馏摘要")

    # ═══════════════════════════════════════
    # 认识论层 - 假说与异常 (模拟预测问题 + 异常检测)
    # ═══════════════════════════════════════

    def add_hypothesis(self, statement: str, project_id: str,
                       confidence: float = 0.5) -> Dict[str, Any]:
        """
        注册可验证假说（模拟的预测问题）

        在模拟准备阶段调用，将simulation_requirement注册为
        正式的Hypothesis节点，后续Agent生成的Claim可作为支持/反驳证据。
        """
        body = {
            "action": "hypothesis",
            "label": statement[:100],
            "confidence": confidence,
            "props": {
                "statement": statement,
                "hypothesis_type": "predictive",
                "status": "proposed",
            },
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/epistemic/inquiry",
            json_body=body,
            operation_name=f"注册假说(project={project_id})"
        )

    def record_anomaly(self, description: str, project_id: str,
                       severity: str = "medium",
                       agent_name: Optional[str] = None) -> Dict[str, Any]:
        """
        记录行为异常（Agent行为与其配置立场不一致）

        当Agent的发言内容与其stance/sentiment_bias矛盾时调用。
        """
        label = description[:100]
        if agent_name:
            label = f"[异常] {agent_name}: {description[:80]}"
        body = {
            "action": "anomaly",
            "label": label,
            "props": {
                "description": description,
                "anomaly_type": "behavioral_inconsistency",
                "severity": severity,
            },
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/epistemic/inquiry",
            json_body=body,
            operation_name=f"记录异常({severity})"
        )

    # ═══════════════════════════════════════
    # 认识论层 - 模式识别 (涌现行为检测)
    # ═══════════════════════════════════════

    def record_pattern(self, name: str, description: str, project_id: str,
                       instance_count: int = 1) -> Dict[str, Any]:
        """
        记录涌现模式（如回声室效应、意见极化等）

        在模拟结束后检测并记录。
        """
        body = {
            "action": "pattern",
            "label": name[:100],
            "props": {
                "name": name,
                "description": description,
                "pattern_type": "emergent",
                "instance_count": instance_count,
            },
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/epistemic/structure",
            json_body=body,
            operation_name=f"记录模式({name})"
        )

    # ═══════════════════════════════════════
    # 意图层 - 目标与决策 (Agent动机建模)
    # ═══════════════════════════════════════

    def create_goal(self, label: str, project_id: str,
                    description: str = "", priority: str = "medium",
                    goal_type: str = "social") -> Dict[str, Any]:
        """
        注册Agent目标（从模拟配置中的stance/sentiment派生）

        在模拟准备阶段调用，为非中立Agent创建Goal节点。
        """
        body = {
            "action": "goal",
            "label": label[:100],
            "props": {
                "description": description,
                "priority": priority,
                "goal_type": goal_type,
                "status": "active",
            },
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/intent/commitment",
            json_body=body,
            operation_name=f"创建目标({label[:30]})"
        )

    def record_decision(self, agent_name: str, description: str,
                        chosen_option: str, rationale: str,
                        project_id: str) -> Dict[str, Any]:
        """
        记录Agent的可观察决策

        当Agent执行高影响力动作（发帖表态、关注/屏蔽）时调用。
        从动作+Agent配置推断决策上下文，无需OASIS内部推理。
        """
        # Step 1: 打开决策
        decision = self._request("POST", "/intent/deliberation", json_body={
            "action": "open_decision",
            "label": description[:100],
            "props": {"description": description},
            "agent_id": project_id,
        }, operation_name=f"打开决策({agent_name})")

        decision_uid = decision.get("uid", "")

        if decision_uid:
            # Step 2: 添加已选选项
            option_uid = ""
            try:
                option_result = self._request("POST", "/intent/deliberation", json_body={
                    "action": "add_option",
                    "decision_uid": decision_uid,
                    "label": chosen_option[:100],
                    "props": {"description": chosen_option},
                    "agent_id": project_id,
                }, operation_name=f"添加选项({agent_name})")
                option_uid = option_result.get("uid", "")
            except Exception as e:
                logger.warning(f"添加决策选项失败: {e}")

            # Step 3: 解决决策（需要chosen_option_uid）
            if option_uid:
                try:
                    self._request("POST", "/intent/deliberation", json_body={
                        "action": "resolve",
                        "decision_uid": decision_uid,
                        "chosen_option_uid": option_uid,
                        "props": {"decision_rationale": rationale},
                        "agent_id": project_id,
                    }, operation_name=f"解决决策({agent_name})")
                except Exception as e:
                    logger.warning(f"解决决策失败: {e}")

        return decision

    # ═══════════════════════════════════════
    # 现实层 - 观察记录 (模拟事件节点)
    # ═══════════════════════════════════════

    def capture_observation(self, content: str, project_id: str,
                            observation_type: str = "simulation_event") -> Dict[str, Any]:
        """
        记录事实观察（模拟轮次结束、关键事件等）
        """
        body = {
            "action": "observation",
            "label": content[:100],
            "props": {
                "content": content,
                "observation_type": observation_type,
            },
            "agent_id": project_id,
        }
        return self._request(
            "POST", "/reality/capture",
            json_body=body,
            operation_name=f"记录观察({observation_type})"
        )

    # ═══════════════════════════════════════
    # 生命周期管理 (替代 Zep graph.delete)
    # ═══════════════════════════════════════

    def delete_node(self, uid: str) -> Dict[str, Any]:
        """软删除节点（级联删除关联边）"""
        return self._request("DELETE", f"/node/{uid}", operation_name=f"删除节点({uid[:12]})")

    def decay_salience(self, uid: str, half_life_secs: int = 86400) -> Dict[str, Any]:
        """衰减单个节点显著度"""
        body = {
            "action": "decay",
            "uid": uid,
            "half_life_secs": half_life_secs,
        }
        return self._request("POST", "/evolve", json_body=body, operation_name=f"衰减({uid[:12]})")

    def decay_project_salience(self, project_id: str, half_life_secs: int = 86400,
                               min_salience: float = 0.1) -> Dict[str, Any]:
        """
        批量衰减项目内所有节点的显著度

        POST /evolve 需要 uid 字段，不支持按 agent_id 批量衰减。
        因此先列出所有节点，然后逐个衰减。
        """
        nodes = self.list_all_nodes(project_id=project_id, max_items=500)
        total_decayed = 0
        for node in nodes:
            uid = node.get("uid", "")
            if not uid:
                continue
            try:
                self.decay_salience(uid, half_life_secs=half_life_secs)
                total_decayed += 1
            except Exception:
                pass

        logger.info(f"批量衰减完成: project={project_id}, decayed={total_decayed}/{len(nodes)}")
        return {"nodes_decayed": total_decayed, "total_nodes": len(nodes)}

    def delete_project_data(self, project_id: str):
        """
        删除项目的所有数据
        由于MindGraph没有per-graph删除，需要遍历所有项目节点逐个删除
        """
        logger.info(f"开始删除项目数据: project_id={project_id}")
        deleted = 0
        offset = 0

        while True:
            nodes = self.list_nodes(project_id=project_id, limit=100, offset=0)
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

        logger.info(f"项目数据删除完成: project_id={project_id}, deleted={deleted}")

    # ═══════════════════════════════════════
    # 全图导出 (用于PanoramaSearch等需要全图读取的场景)
    # ═══════════════════════════════════════

    def export_graph(self) -> Dict[str, Any]:
        """导出完整图谱快照"""
        return self._request("GET", "/export", operation_name="导出图谱")

    def get_graph_statistics(self, project_id: str) -> Dict[str, Any]:
        """获取图谱统计信息（节点数、边数、类型分布）"""
        nodes = self.list_all_nodes(project_id=project_id)
        edges = self.list_all_edges(project_id=project_id)

        # 统计类型分布
        type_counts = {}
        for node in nodes:
            nt = node.get("node_type", node.get("type", "Unknown"))
            type_counts[nt] = type_counts.get(nt, 0) + 1

        return {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "type_distribution": type_counts,
        }
