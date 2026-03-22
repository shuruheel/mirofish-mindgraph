"""
实体读取与过滤服务
从MindGraph图谱中读取节点，筛选出符合预定义实体类型的节点
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
    """实体节点数据结构"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # 相关的边信息
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # 相关的其他节点信息
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
        """获取实体类型（排除默认的Entity标签）"""
        for label in self.labels:
            if label not in ["Entity", "Node", "Unknown"]:
                return label
        return None


@dataclass
class FilteredEntities:
    """过滤后的实体集合"""
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
    实体读取与过滤服务

    主要功能：
    1. 从MindGraph图谱读取所有节点
    2. 筛选出符合预定义实体类型的节点
    3. 获取每个实体的相关边和关联节点信息
    """

    def __init__(self):
        if not Config.MINDGRAPH_API_KEY:
            raise ValueError("MINDGRAPH_API_KEY 未配置")
        self.client = MindGraphClient()

    @staticmethod
    def _normalize_node(mg_node: Dict[str, Any]) -> Dict[str, Any]:
        """将MindGraph节点转换为内部格式"""
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
        """将MindGraph边转换为内部格式"""
        return {
            "uuid": mg_edge.get("uid", ""),
            "name": mg_edge.get("edge_type", mg_edge.get("type", "")),
            "fact": mg_edge.get("label", mg_edge.get("content", "")),
            "source_node_uuid": mg_edge.get("from_uid", mg_edge.get("source_uid", "")),
            "target_node_uuid": mg_edge.get("to_uid", mg_edge.get("target_uid", "")),
            "attributes": mg_edge.get("props", {}),
        }

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        获取图谱的所有节点

        Args:
            graph_id: 图谱ID（MindGraph命名空间）

        Returns:
            节点列表（内部格式）
        """
        logger.info(f"获取图谱 {graph_id} 的所有节点...")
        mg_nodes = self.client.list_all_nodes(project_id=graph_id)
        nodes_data = [self._normalize_node(n) for n in mg_nodes]
        logger.info(f"共获取 {len(nodes_data)} 个节点")
        return nodes_data

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        获取图谱的所有边

        Args:
            graph_id: 图谱ID

        Returns:
            边列表（内部格式）
        """
        logger.info(f"获取图谱 {graph_id} 的所有边...")
        mg_edges = self.client.list_all_edges(project_id=graph_id)
        edges_data = [self._normalize_edge(e) for e in mg_edges]
        logger.info(f"共获取 {len(edges_data)} 条边")
        return edges_data

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """
        获取指定节点的所有相关边

        Args:
            node_uuid: 节点UUID

        Returns:
            边列表
        """
        try:
            neighborhood = self.client.get_neighborhood(uid=node_uuid, depth=1)
            # 从邻居结果中提取边
            edges_raw = neighborhood.get("edges", [])
            return [self._normalize_edge(e) for e in edges_raw]
        except Exception as e:
            logger.warning(f"获取节点 {node_uuid} 的边失败: {str(e)}")
            return []

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True
    ) -> FilteredEntities:
        """
        筛选出符合预定义实体类型的节点

        筛选逻辑：
        - MindGraph节点有node_type字段，检查是否为有意义的实体类型
        - 过滤掉通用类型(Entity, Node, Unknown, Snippet, Chunk等基础类型)

        Args:
            graph_id: 图谱ID
            defined_entity_types: 预定义的实体类型列表
            enrich_with_edges: 是否获取每个实体的相关边信息

        Returns:
            FilteredEntities: 过滤后的实体集合
        """
        logger.info(f"开始筛选图谱 {graph_id} 的实体...")

        # 获取所有节点
        all_nodes = self.get_all_nodes(graph_id)
        total_count = len(all_nodes)

        # 获取所有边
        all_edges = self.get_all_edges(graph_id) if enrich_with_edges else []

        # 构建节点UUID到节点数据的映射
        node_map = {n["uuid"]: n for n in all_nodes}

        # 不作为实体的基础/结构类型
        BASE_TYPES = {
            "Entity", "Node", "Unknown", "Snippet", "Chunk", "Source",
            "Document", "Observation", "Trace", "Session", "Journal",
            "Summary", "Preference", "MemoryPolicy"
        }

        # 筛选符合条件的实体
        filtered_entities = []
        entity_types_found = set()

        for node in all_nodes:
            labels = node.get("labels", [])

            # 获取实体类型（排除基础类型）
            custom_labels = [l for l in labels if l not in BASE_TYPES]

            if not custom_labels:
                continue

            # 如果指定了预定义类型，检查是否匹配
            if defined_entity_types:
                matching_labels = [l for l in custom_labels if l in defined_entity_types]
                if not matching_labels:
                    continue
                entity_type = matching_labels[0]
            else:
                entity_type = custom_labels[0]

            entity_types_found.add(entity_type)

            # 创建实体节点对象
            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )

            # 获取相关边和节点
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
                        })

                entity.related_nodes = related_nodes

            filtered_entities.append(entity)

        logger.info(f"筛选完成: 总节点 {total_count}, 符合条件 {len(filtered_entities)}, "
                   f"实体类型: {entity_types_found}")

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
        获取单个实体及其完整上下文（边和关联节点）
        """
        try:
            mg_node = self.client.get_node(uid=entity_uuid)
            if not mg_node:
                return None

            node = self._normalize_node(mg_node)

            # 获取节点的边
            edges = self.get_node_edges(entity_uuid)

            # 获取所有节点用于关联查找
            all_nodes = self.get_all_nodes(graph_id)
            node_map_data = {n["uuid"]: n for n in all_nodes}

            # 处理相关边和节点
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
            logger.error(f"获取实体 {entity_uuid} 失败: {str(e)}")
            return None

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True
    ) -> List[EntityNode]:
        """获取指定类型的所有实体"""
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges
        )
        return result.entities
