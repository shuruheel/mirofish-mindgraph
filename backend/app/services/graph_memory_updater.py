"""
图谱记忆更新服务
将模拟中的Agent活动动态更新到MindGraph图谱中
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from ..config import Config
from ..utils.logger import get_logger
from ..utils.mindgraph_client import MindGraphClient

logger = get_logger('mirofish.graph_memory_updater')


@dataclass
class AgentActivity:
    """Agent活动记录"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str

    def to_episode_text(self) -> str:
        """
        将活动转换为文本描述

        采用自然语言描述格式，让MindGraph能够从中提取实体和关系
        不添加模拟相关的前缀，避免误导图谱更新
        """
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }

        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()

        return f"{self.agent_name}: {description}"

    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"发布了一条帖子：「{content}」"
        return "发布了一条帖子"

    def _describe_like_post(self) -> str:
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if post_content and post_author:
            return f"点赞了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"点赞了一条帖子：「{post_content}」"
        elif post_author:
            return f"点赞了{post_author}的一条帖子"
        return "点赞了一条帖子"

    def _describe_dislike_post(self) -> str:
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if post_content and post_author:
            return f"踩了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"踩了一条帖子：「{post_content}」"
        elif post_author:
            return f"踩了{post_author}的一条帖子"
        return "踩了一条帖子"

    def _describe_repost(self) -> str:
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        if original_content and original_author:
            return f"转发了{original_author}的帖子：「{original_content}」"
        elif original_content:
            return f"转发了一条帖子：「{original_content}」"
        elif original_author:
            return f"转发了{original_author}的一条帖子"
        return "转发了一条帖子"

    def _describe_quote_post(self) -> str:
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        base = ""
        if original_content and original_author:
            base = f"引用了{original_author}的帖子「{original_content}」"
        elif original_content:
            base = f"引用了一条帖子「{original_content}」"
        elif original_author:
            base = f"引用了{original_author}的一条帖子"
        else:
            base = "引用了一条帖子"
        if quote_content:
            base += f"，并评论道：「{quote_content}」"
        return base

    def _describe_follow(self) -> str:
        target_user_name = self.action_args.get("target_user_name", "")
        if target_user_name:
            return f"关注了用户「{target_user_name}」"
        return "关注了一个用户"

    def _describe_create_comment(self) -> str:
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if content:
            if post_content and post_author:
                return f"在{post_author}的帖子「{post_content}」下评论道：「{content}」"
            elif post_content:
                return f"在帖子「{post_content}」下评论道：「{content}」"
            elif post_author:
                return f"在{post_author}的帖子下评论道：「{content}」"
            return f"评论道：「{content}」"
        return "发表了评论"

    def _describe_like_comment(self) -> str:
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        if comment_content and comment_author:
            return f"点赞了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"点赞了一条评论：「{comment_content}」"
        elif comment_author:
            return f"点赞了{comment_author}的一条评论"
        return "点赞了一条评论"

    def _describe_dislike_comment(self) -> str:
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        if comment_content and comment_author:
            return f"踩了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"踩了一条评论：「{comment_content}」"
        elif comment_author:
            return f"踩了{comment_author}的一条评论"
        return "踩了一条评论"

    def _describe_search(self) -> str:
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"搜索了「{query}」" if query else "进行了搜索"

    def _describe_search_user(self) -> str:
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"搜索了用户「{query}」" if query else "搜索了用户"

    def _describe_mute(self) -> str:
        target_user_name = self.action_args.get("target_user_name", "")
        if target_user_name:
            return f"屏蔽了用户「{target_user_name}」"
        return "屏蔽了一个用户"

    def _describe_generic(self) -> str:
        return f"执行了{self.action_type}操作"


class GraphMemoryUpdater:
    """
    图谱记忆更新器

    监控模拟的actions日志文件，将新的agent活动实时更新到MindGraph图谱中。
    按平台分组，每累积BATCH_SIZE条活动后批量发送到MindGraph。
    """

    BATCH_SIZE = 5
    PLATFORM_DISPLAY_NAMES = {
        'twitter': '世界1',
        'reddit': '世界2',
    }
    SEND_INTERVAL = 0.5

    # 内容性动作类型 — 发言/评论/引用 → 结构化声明(Claim)
    CONTENT_ACTIONS = {"CREATE_POST", "CREATE_COMMENT", "QUOTE_POST"}
    # 最小内容长度，低于此阈值的不作为声明处理
    MIN_CLAIM_CONTENT_LENGTH = 20
    # 高影响力动作阈值 — 超过此长度的内容动作额外记录为Decision
    HIGH_IMPACT_CONTENT_LENGTH = 80
    # 每批最多记录的决策数（避免API调用爆炸）
    MAX_DECISIONS_PER_BATCH = 3
    # 社交决策动作 — 记录为Decision
    SOCIAL_DECISION_ACTIONS = {"FOLLOW", "MUTE"}
    # 正面/负面标记词（用于简单异常检测）
    POSITIVE_MARKERS = {"支持", "赞同", "好的", "同意", "正确", "很好", "不错", "认同"}
    NEGATIVE_MARKERS = {"反对", "错误", "不同意", "反驳", "批评", "失败", "糟糕", "不行"}

    def __init__(self, graph_id: str, minutes_per_round: int = 60,
                 agent_node_uids: Optional[Dict[str, str]] = None,
                 source: str = "upload",
                 simulation_dir: Optional[str] = None):
        self.graph_id = graph_id
        self.minutes_per_round = minutes_per_round
        self.source = source
        self._simulation_dir = simulation_dir

        if not Config.MINDGRAPH_API_KEY:
            raise ValueError("MINDGRAPH_API_KEY未配置")

        self.client = MindGraphClient()

        # Agent名称 → MindGraph Agent节点UID映射
        # 用于在摄入后创建 Agent→提取节点 的AUTHORED边
        self._agent_node_uids: Dict[str, str] = agent_node_uids or {}

        # MindGraph会话（用于跟踪模拟生命周期）
        self._session_uid: Optional[str] = None

        # 认知节点UID追踪（用于模拟后蒸馏，包括Claim/Question/Observation等）
        self._created_epistemic_uids: List[str] = []

        self._activity_queue: Queue = Queue()
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()

        self._running = False
        self._worker_thread: Optional[threading.Thread] = None

        self._total_activities = 0
        self._total_sent = 0
        self._total_items_sent = 0
        self._total_claims = 0
        self._total_traces = 0
        self._failed_count = 0
        self._skipped_count = 0

        logger.info(f"GraphMemoryUpdater 初始化完成: graph_id={graph_id}, source={source}, batch_size={self.BATCH_SIZE}")

    def _get_platform_display_name(self, platform: str) -> str:
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)

    def _session_uid_path(self) -> Optional[str]:
        """Path to persist session_uid for recovery after restart."""
        if self._simulation_dir:
            return os.path.join(self._simulation_dir, "mindgraph_session_uid.txt")
        return None

    def _save_session_uid(self):
        path = self._session_uid_path()
        if path and self._session_uid:
            try:
                with open(path, 'w') as f:
                    f.write(self._session_uid)
            except Exception:
                pass

    def _close_orphaned_session(self):
        """Attempt to close a session left over from a previous run."""
        path = self._session_uid_path()
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, 'r') as f:
                old_uid = f.read().strip()
            if old_uid:
                self.client.close_session(old_uid, project_id=self.graph_id)
                logger.info(f"已关闭孤立MindGraph会话: {old_uid}")
        except Exception as e:
            logger.debug(f"关闭孤立会话失败（可能已过期）: {e}")
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    def start(self):
        if self._running:
            return

        # Close any orphaned session from a previous run (e.g. after server restart)
        self._close_orphaned_session()

        # 打开MindGraph会话 — 包裹整个模拟生命周期
        try:
            self._session_uid = self.client.open_session(
                project_id=self.graph_id,
                session_name=f"Simulation {self.graph_id}"
            )
            self._save_session_uid()
            logger.info(f"MindGraph会话已打开: session_uid={self._session_uid}")
        except Exception as e:
            logger.warning(f"打开MindGraph会话失败（将降级为纯文本摄入）: {e}")
            self._session_uid = None

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"GraphMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"GraphMemoryUpdater 已启动: graph_id={self.graph_id}")

    def stop(self):
        self._running = False
        self._flush_remaining()

        # 模拟结束后蒸馏+模式检测
        self._distill_simulation()

        # 关闭MindGraph会话
        if self._session_uid:
            try:
                self.client.close_session(self._session_uid, project_id=self.graph_id)
                logger.info(f"MindGraph会话已关闭: session_uid={self._session_uid}")
            except Exception as e:
                logger.warning(f"关闭MindGraph会话失败: {e}")

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        logger.info(f"GraphMemoryUpdater 已停止: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"claims={self._total_claims}, traces={self._total_traces}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")

    def add_activity(self, activity: AgentActivity):
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"添加活动到图谱队列: {activity.agent_name} - {activity.action_type}")

    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        if "event_type" in data:
            return
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        self.add_activity(activity)

    def _worker_loop(self):
        while self._running or not self._activity_queue.empty():
            try:
                try:
                    activity = self._activity_queue.get(timeout=1)
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            self._send_batch_activities(batch, platform)
                            time.sleep(self.SEND_INTERVAL)
                except Empty:
                    pass
            except Exception as e:
                logger.error(f"工作循环异常: {e}")
                time.sleep(1)

    def _get_content(self, activity: AgentActivity) -> str:
        """提取活动的核心文本内容"""
        args = activity.action_args
        if activity.action_type == "CREATE_POST":
            return args.get("content", "")
        elif activity.action_type == "CREATE_COMMENT":
            return args.get("content", "")
        elif activity.action_type == "QUOTE_POST":
            return args.get("quote_content", "") or args.get("content", "")
        return ""

    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        批量发送活动到MindGraph图谱

        结构化写入策略：
        - 内容性动作(CREATE_POST/COMMENT/QUOTE) → Journal节点（Memory层）
        - 社交动作(LIKE/FOLLOW/REPOST等) → Trace条目（Memory层）
        - 高影响力决策(FOLLOW/MUTE) → Decision/Option节点（Intent层）
        """
        if not activities:
            return

        display_name = self._get_platform_display_name(platform)
        trace_texts = []
        journals_sent = 0
        traces_sent = 0
        decisions_in_batch = 0  # Rate-limit decisions per batch

        # Collect links to create in batch after all journals are written
        pending_links = []  # [(from_uid, to_uid)]

        for activity in activities:
            try:
                content = self._get_content(activity)

                # 内容性动作 → Journal节点（Memory层，无认知提取）
                if (activity.action_type in self.CONTENT_ACTIONS
                        and len(content) >= self.MIN_CLAIM_CONTENT_LENGTH):

                    journal_content = f"{activity.agent_name}: {content}"
                    result = self.client.create_journal(
                        content=journal_content,
                        project_id=self.graph_id,
                        journal_type="simulation_post",
                        tags=[platform, activity.action_type,
                              f"round_{activity.round_num}"],
                        session_uid=self._session_uid,
                    )

                    # Collect Agent → Journal link (created in batch below)
                    journal_uid = result.get("uid", "") if isinstance(result, dict) else ""
                    agent_uid = self._agent_node_uids.get(activity.agent_name)
                    if journal_uid and agent_uid:
                        pending_links.append((agent_uid, journal_uid))

                    journals_sent += 1
                    self._total_claims += 1

                    # 异常检测：Agent行为是否与立场矛盾
                    self._check_anomaly(activity, content)
                    # 高影响力决策记录（rate-limited）
                    if decisions_in_batch < self.MAX_DECISIONS_PER_BATCH:
                        if self._record_decision(activity, content):
                            decisions_in_batch += 1
                else:
                    # 社交决策记录（FOLLOW/MUTE, rate-limited）
                    if (activity.action_type in self.SOCIAL_DECISION_ACTIONS
                            and decisions_in_batch < self.MAX_DECISIONS_PER_BATCH):
                        if self._record_decision(activity, ""):
                            decisions_in_batch += 1
                    # 社交动作 → 收集为trace文本
                    trace_texts.append(activity.to_episode_text())

            except Exception as e:
                logger.warning(f"结构化写入失败，降级为trace: {activity.agent_name} {activity.action_type}: {e}")
                trace_texts.append(activity.to_episode_text())

        # Batch create Agent → Journal links (single API call)
        if pending_links:
            try:
                batch_edges = [
                    {"from_uid": from_uid, "to_uid": to_uid, "edge_type": "AUTHORED"}
                    for from_uid, to_uid in pending_links
                ]
                result = self.client.batch_create(edges=batch_edges)
                errors = result.get("errors", [])
                if errors:
                    logger.debug(f"Batch link creation: {len(errors)} errors")
            except Exception as e:
                logger.debug(f"Batch link creation failed, falling back to individual: {e}")
                # Fallback to individual link creation
                for from_uid, to_uid in pending_links:
                    try:
                        self.client.add_link(
                            from_uid=from_uid, to_uid=to_uid,
                            edge_type="AUTHORED", project_id=self.graph_id,
                        )
                    except Exception:
                        pass

        # 批量写入trace条目
        if trace_texts:
            combined_trace = "\n".join(trace_texts)
            try:
                if self._session_uid:
                    self.client.trace_session(
                        session_uid=self._session_uid,
                        content=combined_trace,
                        project_id=self.graph_id,
                        trace_type="simulation_activity"
                    )
                else:
                    # 降级：无会话时直接创建Journal
                    self.client.create_journal(
                        content=combined_trace,
                        project_id=self.graph_id,
                        journal_type="simulation_trace",
                    )
                traces_sent = len(trace_texts)
                self._total_traces += traces_sent
            except Exception as e:
                logger.error(f"trace写入失败: {e}")
                self._failed_count += 1

        self._total_sent += 1
        self._total_items_sent += len(activities)
        queue_remaining = self._activity_queue.qsize()
        logger.info(
            f"成功发送 {len(activities)} 条{display_name}活动 "
            f"(journals={journals_sent}, traces={traces_sent}, decisions={decisions_in_batch}) "
            f"到图谱 {self.graph_id} [queue={queue_remaining}]"
        )

    def _flush_remaining(self):
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break

        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"发送{display_name}平台剩余的 {len(buffer)} 条活动")
                    self._send_batch_activities(buffer, platform)
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []

    # ═══════════════════════════════════════
    # Agent→节点 边创建
    # ═══════════════════════════════════════

    def _link_agent_to_nodes(self, agent_uid: str, target_uids: List[str],
                             edge_type: str):
        """
        创建Agent节点到目标节点的边（batch API优先）

        使用batch_create一次创建所有边，失败时回退到逐条创建。
        """
        if not target_uids:
            return
        batch_edges = [
            {"from_uid": agent_uid, "to_uid": uid, "edge_type": edge_type}
            for uid in target_uids
        ]
        try:
            self.client.batch_create(edges=batch_edges)
        except Exception as e:
            logger.debug(f"批量创建{edge_type}边失败，回退逐条: {e}")
            for uid in target_uids:
                try:
                    self.client.add_link(
                        from_uid=agent_uid, to_uid=uid,
                        edge_type=edge_type, project_id=self.graph_id,
                    )
                except Exception:
                    pass

    # ═══════════════════════════════════════
    # 轮间衰减 + 模拟后蒸馏 + 异常检测 + 决策记录
    # ═══════════════════════════════════════

    def decay_round(self, round_num: int):
        """
        轮间显著度衰减 — 模拟记忆的自然遗忘

        DISABLED: decay() is a global operation that degrades salience across
        the entire MindGraph graph (book knowledge + simulation data), not
        just simulation-created nodes. This harms retrieval quality for the
        graph context provider. Simulations are short-lived; natural recency
        bias in retrieval handles salience implicitly.
        """
        logger.debug(f"跳过轮间衰减 (全局操作已禁用): round={round_num}")

    def record_round_end(self, round_num: int, platform: str,
                         actions_count: int = 0):
        """记录轮次结束为Observation节点（via batch API）"""
        display_name = self._get_platform_display_name(platform)
        content = f"第{round_num}轮{display_name}模拟完成，共{actions_count}个动作"
        try:
            self.client.batch_create(nodes=[{
                "label": content[:100],
                "props": {
                    "_type": "Observation",
                    "content": content,
                    "observation_type": "simulation_event",
                },
                "agent_id": self.graph_id,
            }])
        except Exception as e:
            logger.debug(f"记录轮次观察失败: {e}")

    def _check_anomaly(self, activity: AgentActivity, content: str):
        """
        检测Agent行为是否与其配置立场矛盾

        简单启发式：如果opposing agent发正面内容或supportive agent发负面内容
        """
        stance = activity.action_args.get("stance", "neutral")
        sentiment = activity.action_args.get("sentiment_bias", 0.0)

        if stance == "neutral" or not content:
            return

        has_positive = any(m in content for m in self.POSITIVE_MARKERS)
        has_negative = any(m in content for m in self.NEGATIVE_MARKERS)

        is_anomaly = False
        if stance == "opposing" and sentiment < -0.3 and has_positive and not has_negative:
            is_anomaly = True
        elif stance == "supportive" and sentiment > 0.3 and has_negative and not has_positive:
            is_anomaly = True

        if is_anomaly:
            try:
                anomaly_result = self.client.record_anomaly(
                    description=(
                        f"{activity.agent_name} (stance={stance}, sentiment={sentiment}) "
                        f"发表了与立场不一致的内容: {content[:100]}"
                    ),
                    project_id=self.graph_id,
                    severity="medium",
                    agent_name=activity.agent_name,
                )
                logger.info(f"检测到行为异常: {activity.agent_name} ({stance})")
                # 链接 Agent → Anomaly
                agent_uid = self._agent_node_uids.get(activity.agent_name)
                anomaly_uid = anomaly_result.get("uid", "") if isinstance(anomaly_result, dict) else ""
                if agent_uid and anomaly_uid:
                    self._link_agent_to_nodes(agent_uid, [anomaly_uid], "EXHIBITED")
            except Exception as e:
                logger.debug(f"记录异常失败: {e}")

    def _record_decision(self, activity: AgentActivity, content: str) -> bool:
        """
        将高影响力动作记录为Decision节点

        Uses batch API to create Decision + Option nodes in a single call,
        then links Agent→Decision via batch edges.

        Returns True if a decision was recorded (for rate-limiting).
        """
        stance = activity.action_args.get("stance", "neutral")
        sentiment = activity.action_args.get("sentiment_bias", 0.0)
        agent_uid = self._agent_node_uids.get(activity.agent_name)

        if activity.action_type in self.CONTENT_ACTIONS and len(content) >= self.HIGH_IMPACT_CONTENT_LENGTH:
            description = f"{activity.agent_name} decided to publicly comment"
            rationale = f"Agent stance: {stance}, sentiment: {sentiment}"
            try:
                # Batch create Decision + Option nodes in one call
                result = self.client.batch_create(nodes=[
                    {
                        "label": description[:100],
                        "props": {
                            "_type": "Decision",
                            "description": description,
                            "rationale": rationale,
                        },
                        "agent_id": self.graph_id,
                    },
                    {
                        "label": content[:100],
                        "props": {
                            "_type": "Option",
                            "description": content[:200],
                        },
                        "agent_id": self.graph_id,
                    },
                ])
                # Link Agent → Decision if we got UIDs back
                node_uids = result.get("node_uids", []) if isinstance(result, dict) else []
                if agent_uid and node_uids:
                    decision_uid = node_uids[0] if len(node_uids) > 0 else ""
                    option_uid = node_uids[1] if len(node_uids) > 1 else ""
                    edges = []
                    if decision_uid:
                        edges.append({"from_uid": agent_uid, "to_uid": decision_uid, "edge_type": "DECIDED"})
                    if decision_uid and option_uid:
                        edges.append({"from_uid": decision_uid, "to_uid": option_uid, "edge_type": "HasOption"})
                    if edges:
                        try:
                            self.client.batch_create(edges=edges)
                        except Exception:
                            pass
                return True
            except Exception as e:
                logger.debug(f"记录决策失败: {e}")
                return False

        elif activity.action_type in self.SOCIAL_DECISION_ACTIONS:
            target = activity.action_args.get("target_user_name", "unknown")
            verb = "follow" if activity.action_type == "FOLLOW" else "mute"
            description = f"{activity.agent_name} decided to {verb} {target}"
            try:
                result = self.client.batch_create(nodes=[
                    {
                        "label": description[:100],
                        "props": {
                            "_type": "Decision",
                            "description": description,
                            "rationale": "Social alignment decision",
                        },
                        "agent_id": self.graph_id,
                    },
                ])
                node_uids = result.get("node_uids", []) if isinstance(result, dict) else []
                if agent_uid and node_uids:
                    try:
                        self.client.batch_create(edges=[
                            {"from_uid": agent_uid, "to_uid": node_uids[0], "edge_type": "DECIDED"}
                        ])
                    except Exception:
                        pass
                return True
            except Exception as e:
                logger.debug(f"记录社交决策失败: {e}")
                return False

        return False

    def _distill_simulation(self):
        """
        模拟结束后蒸馏 — 创建Summary节点 + 检测涌现模式

        在stop()中关闭会话前调用。
        """
        if not self._created_epistemic_uids:
            logger.info("无Claim节点，跳过蒸馏")
            return

        # 蒸馏：将所有Claim汇总为Summary
        try:
            self.client.distill(
                label=f"Simulation Summary: {self.graph_id}",
                source_uids=self._created_epistemic_uids[:50],  # 限制UID数量
                project_id=self.graph_id,
                content=f"基于{len(self._created_epistemic_uids)}个Agent声明的自动蒸馏摘要",
            )
            logger.info(f"模拟蒸馏完成: {len(self._created_epistemic_uids)} claims → summary")
        except Exception as e:
            logger.warning(f"模拟蒸馏失败: {e}")

        # 简单模式检测
        if self._total_claims > 5:
            # 高不确定性模式：大多数claims置信度低
            # (需要追踪置信度，这里用总数做粗略估计)
            try:
                self.client.record_pattern(
                    name="模拟活动模式",
                    description=(
                        f"本次模拟共产生{self._total_claims}个结构化声明, "
                        f"{self._total_traces}个社交追踪, "
                        f"{self._total_activities}个总活动"
                    ),
                    project_id=self.graph_id,
                    instance_count=self._total_claims,
                )
            except Exception as e:
                logger.debug(f"记录模式失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,
            "batches_sent": self._total_sent,
            "items_sent": self._total_items_sent,
            "failed_count": self._failed_count,
            "skipped_count": self._skipped_count,
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,
            "running": self._running,
        }


class GraphMemoryManager:
    """管理多个模拟的图谱记忆更新器"""

    _updaters: Dict[str, GraphMemoryUpdater] = {}
    _lock = threading.Lock()

    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str,
                       minutes_per_round: int = 60,
                       agent_node_uids: Optional[Dict[str, str]] = None,
                       source: str = "upload",
                       simulation_dir: Optional[str] = None) -> GraphMemoryUpdater:
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            updater = GraphMemoryUpdater(
                graph_id,
                minutes_per_round=minutes_per_round,
                agent_node_uids=agent_node_uids,
                source=source,
                simulation_dir=simulation_dir,
            )
            updater.start()
            cls._updaters[simulation_id] = updater
            logger.info(
                f"创建图谱记忆更新器: simulation_id={simulation_id}, "
                f"graph_id={graph_id}, source={source}, agent_nodes={len(agent_node_uids or {})}"
            )
            return updater

    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[GraphMemoryUpdater]:
        return cls._updaters.get(simulation_id)

    @classmethod
    def stop_updater(cls, simulation_id: str):
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"已停止图谱记忆更新器: simulation_id={simulation_id}")

    _stop_all_done = False

    @classmethod
    def stop_all(cls):
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"停止更新器失败: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("已停止所有图谱记忆更新器")

    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        return {
            sim_id: updater.get_stats()
            for sim_id, updater in cls._updaters.items()
        }
