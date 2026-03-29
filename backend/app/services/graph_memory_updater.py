"""
Graph memory update service
Dynamically updates agent activities from simulations into the MindGraph graph
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
    """Agent activity record"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str

    def to_episode_text(self) -> str:
        """
        Convert activity to text description

        Uses natural language description format so MindGraph can extract entities and relationships from it
        No simulation-related prefixes added to avoid misleading graph updates
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

        return f'{self.agent_name}: {description}'

    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f'published a post: "{content}"'
        return "published a post"

    def _describe_like_post(self) -> str:
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if post_content and post_author:
            return f"liked {post_author}'s post: \"{post_content}\""
        elif post_content:
            return f'liked a post: "{post_content}"'
        elif post_author:
            return f"liked {post_author}'s post"
        return "liked a post"

    def _describe_dislike_post(self) -> str:
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if post_content and post_author:
            return f"disliked {post_author}'s post: \"{post_content}\""
        elif post_content:
            return f'disliked a post: "{post_content}"'
        elif post_author:
            return f"disliked {post_author}'s post"
        return "disliked a post"

    def _describe_repost(self) -> str:
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        if original_content and original_author:
            return f"reposted {original_author}'s post: \"{original_content}\""
        elif original_content:
            return f'reposted a post: "{original_content}"'
        elif original_author:
            return f"reposted {original_author}'s post"
        return "reposted a post"

    def _describe_quote_post(self) -> str:
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        base = ""
        if original_content and original_author:
            base = f'quoted {original_author}\'s post: "{original_content}"'
        elif original_content:
            base = f'quoted a post: "{original_content}"'
        elif original_author:
            base = f"quoted {original_author}'s post"
        else:
            base = "quoted a post"
        if quote_content:
            base += f', and commented: "{quote_content}"'
        return base

    def _describe_follow(self) -> str:
        target_user_name = self.action_args.get("target_user_name", "")
        if target_user_name:
            return f'followed user "{target_user_name}"'
        return "followed a user"

    def _describe_create_comment(self) -> str:
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        if content:
            if post_content and post_author:
                return f'on {post_author}\'s post "{post_content}" commented: "{content}"'
            elif post_content:
                return f'on post "{post_content}" commented: "{content}"'
            elif post_author:
                return f'on {post_author}\'s post commented: "{content}"'
            return f'commented: "{content}"'
        return "posted a comment"

    def _describe_like_comment(self) -> str:
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        if comment_content and comment_author:
            return f"liked {comment_author}'s comment: \"{comment_content}\""
        elif comment_content:
            return f'liked a comment: "{comment_content}"'
        elif comment_author:
            return f"liked {comment_author}'s comment"
        return "liked a comment"

    def _describe_dislike_comment(self) -> str:
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        if comment_content and comment_author:
            return f"disliked {comment_author}'s comment: \"{comment_content}\""
        elif comment_content:
            return f'disliked a comment: "{comment_content}"'
        elif comment_author:
            return f"disliked {comment_author}'s comment"
        return "disliked a comment"

    def _describe_search(self) -> str:
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f'searched for "{query}"' if query else "performed a search"

    def _describe_search_user(self) -> str:
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f'searched for user "{query}"' if query else "searched for a user"

    def _describe_mute(self) -> str:
        target_user_name = self.action_args.get("target_user_name", "")
        if target_user_name:
            return f'muted user "{target_user_name}"'
        return "muted a user"

    def _describe_generic(self) -> str:
        return f"performed {self.action_type} action"


class GraphMemoryUpdater:
    """
    Graph memory updater

    Monitors simulation action log files and updates new agent activities to the MindGraph graph in real-time.
    Groups by platform, batch-sending to MindGraph every BATCH_SIZE activities.
    """

    BATCH_SIZE = 5
    PLATFORM_DISPLAY_NAMES = {
        'twitter': 'World 1',
        'reddit': 'World 2',
    }
    SEND_INTERVAL = 0.5

    # Content action types -- posts/comments/quotes -> structured claims (Claim)
    CONTENT_ACTIONS = {"CREATE_POST", "CREATE_COMMENT", "QUOTE_POST"}
    # Minimum content length, below this threshold not treated as claims
    MIN_CLAIM_CONTENT_LENGTH = 20
    # High-impact action threshold -- content actions exceeding this length also recorded as Decision
    HIGH_IMPACT_CONTENT_LENGTH = 80
    # Max decisions per batch (to avoid API call explosion)
    MAX_DECISIONS_PER_BATCH = 3
    # Social decision actions -- recorded as Decision
    SOCIAL_DECISION_ACTIONS = {"FOLLOW", "MUTE"}
    # Positive/negative marker words (for simple anomaly detection)
    POSITIVE_MARKERS = {"support", "agree", "good", "correct", "great", "approve", "endorse", "affirm"}
    NEGATIVE_MARKERS = {"oppose", "wrong", "disagree", "refute", "criticize", "fail", "terrible", "reject"}

    def __init__(self, graph_id: str, minutes_per_round: int = 60,
                 agent_node_uids: Optional[Dict[str, str]] = None,
                 source: str = "upload",
                 simulation_dir: Optional[str] = None):
        self.graph_id = graph_id
        self.minutes_per_round = minutes_per_round
        self.source = source
        self._simulation_dir = simulation_dir

        if not Config.MINDGRAPH_API_KEY:
            raise ValueError("MINDGRAPH_API_KEY is not configured")

        self.client = MindGraphClient()

        # Agent name -> MindGraph Agent node UID mapping
        # Used to create Agent->extracted node AUTHORED edges after ingestion
        self._agent_node_uids: Dict[str, str] = agent_node_uids or {}
        self._agent_uids_loaded: bool = bool(self._agent_node_uids)

        # MindGraph session (for tracking simulation lifecycle)
        self._session_uid: Optional[str] = None

        # Cognitive node UID tracking (for post-simulation distillation, including Claim/Question/Observation etc.)
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

        logger.info(f"GraphMemoryUpdater initialized: graph_id={graph_id}, source={source}, batch_size={self.BATCH_SIZE}")

    def _try_load_agent_uids(self):
        """Hot-reload agent_node_uids.json from disk if we started with an empty mapping.

        Phase 4 of prepare_simulation writes the file after profile generation,
        which can take minutes. If the simulation starts before Phase 4 finishes,
        the updater is initialized with an empty dict. This method retries on
        each batch send until the file appears.
        """
        if self._agent_uids_loaded or not self._simulation_dir:
            return
        uids_path = os.path.join(self._simulation_dir, "agent_node_uids.json")
        if not os.path.exists(uids_path):
            return
        try:
            with open(uids_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if loaded:
                self._agent_node_uids = loaded
                self._agent_uids_loaded = True
                logger.info(f"Hot-loaded {len(loaded)} agent node UIDs from disk")
        except Exception as e:
            logger.debug(f"Failed to hot-load agent_node_uids.json: {e}")

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
                logger.info(f"Closed orphaned MindGraph session: {old_uid}")
        except Exception as e:
            logger.debug(f"Failed to close orphaned session (may have expired): {e}")
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

        # Open MindGraph session -- wraps the entire simulation lifecycle
        try:
            self._session_uid = self.client.open_session(
                project_id=self.graph_id,
                session_name=f"Simulation {self.graph_id}"
            )
            self._save_session_uid()
            logger.info(f"MindGraph session opened: session_uid={self._session_uid}")
        except Exception as e:
            logger.warning(f"Failed to open MindGraph session (will fall back to plain text ingestion): {e}")
            self._session_uid = None

        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"GraphMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"GraphMemoryUpdater started: graph_id={self.graph_id}")

    def stop(self):
        self._running = False
        self._flush_remaining()

        # Post-simulation distillation + pattern detection
        self._distill_simulation()

        # Close MindGraph session
        if self._session_uid:
            try:
                self.client.close_session(self._session_uid, project_id=self.graph_id)
                logger.info(f"MindGraph session closed: session_uid={self._session_uid}")
            except Exception as e:
                logger.warning(f"Failed to close MindGraph session: {e}")

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        logger.info(f"GraphMemoryUpdater stopped: graph_id={self.graph_id}, "
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
        logger.debug(f"Adding activity to graph queue: {activity.agent_name} - {activity.action_type}")

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
                logger.error(f"Worker loop exception: {e}")
                time.sleep(1)

    def _get_content(self, activity: AgentActivity) -> str:
        """Extract core text content from activity"""
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
        Batch-send activities to MindGraph graph

        Structured write strategy:
        - Content actions (CREATE_POST/COMMENT/QUOTE) -> Journal nodes (Memory layer)
        - Social actions (LIKE/FOLLOW/REPOST etc.) -> Trace entries (Memory layer)
        - High-impact decisions (FOLLOW/MUTE) -> Decision/Option nodes (Intent layer)
        """
        if not activities:
            return

        # Hot-reload agent UIDs if we started before Phase 4 completed
        self._try_load_agent_uids()

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

                # Content actions -> Journal nodes (Memory layer, no cognitive extraction)
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
                    elif not journal_uid:
                        logger.warning(f"Journal creation returned no UID: {activity.agent_name}")
                    elif not agent_uid:
                        logger.warning(f"Agent node UID not found: '{activity.agent_name}' (registered: {len(self._agent_node_uids)})")

                    journals_sent += 1
                    self._total_claims += 1

                    # Anomaly detection: Check if agent behavior contradicts stance
                    self._check_anomaly(activity, content)
                    # High-impact decision recording (rate-limited)
                    if decisions_in_batch < self.MAX_DECISIONS_PER_BATCH:
                        if self._record_decision(activity, content):
                            decisions_in_batch += 1
                else:
                    # Social decision recording (FOLLOW/MUTE, rate-limited)
                    if (activity.action_type in self.SOCIAL_DECISION_ACTIONS
                            and decisions_in_batch < self.MAX_DECISIONS_PER_BATCH):
                        if self._record_decision(activity, ""):
                            decisions_in_batch += 1
                    # Social actions -> collect as trace text
                    trace_texts.append(activity.to_episode_text())

            except Exception as e:
                logger.warning(f"Structured write failed, falling back to trace: {activity.agent_name} {activity.action_type}: {e}")
                trace_texts.append(activity.to_episode_text())

        # Batch create Agent → Journal links (single API call)
        if pending_links:
            logger.info(f"Creating {len(pending_links)}  AUTHORED edges")
            try:
                batch_edges = [
                    {"from_uid": from_uid, "to_uid": to_uid, "edge_type": "AUTHORED"}
                    for from_uid, to_uid in pending_links
                ]
                result = self.client.batch_create(edges=batch_edges)
                errors = result.get("errors", [])
                if errors:
                    logger.warning(f"Batch AUTHORED edge creation: {len(errors)} errors: {errors[:3]}")
            except Exception as e:
                logger.warning(f"Batch AUTHORED edge creation failed, falling back to individual: {e}")
                for from_uid, to_uid in pending_links:
                    try:
                        self.client.add_link(
                            from_uid=from_uid, to_uid=to_uid,
                            edge_type="AUTHORED", project_id=self.graph_id,
                        )
                    except Exception as link_err:
                        logger.warning(f"Individual AUTHORED edge failed: {from_uid} -> {to_uid}: {link_err}")

        # Batch-write trace entries
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
                    # Fallback: create Journal directly when no session
                    self.client.create_journal(
                        content=combined_trace,
                        project_id=self.graph_id,
                        journal_type="simulation_trace",
                    )
                traces_sent = len(trace_texts)
                self._total_traces += traces_sent
            except Exception as e:
                logger.error(f"Trace write failed: {e}")
                self._failed_count += 1

        self._total_sent += 1
        self._total_items_sent += len(activities)
        queue_remaining = self._activity_queue.qsize()
        logger.info(
            f"Successfully sent {len(activities)} {display_name}activities "
            f"(journals={journals_sent}, traces={traces_sent}, decisions={decisions_in_batch}) "
            f"to graph {self.graph_id} [queue={queue_remaining}]"
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
                    logger.info(f"Sending {display_name}platform remaining {len(buffer)} activities")
                    self._send_batch_activities(buffer, platform)
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []

    # ═══════════════════════════════════════
    # Agent->Node edge creation
    # ═══════════════════════════════════════

    def _link_agent_to_nodes(self, agent_uid: str, target_uids: List[str],
                             edge_type: str):
        """
        Create edges from Agent node to target nodes (batch API preferred)

        Uses batch_create to create all edges at once, falls back to individual creation on failure.
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
            logger.debug(f"Batch creating {edge_type} edges failed, falling back to individual: {e}")
            for uid in target_uids:
                try:
                    self.client.add_link(
                        from_uid=agent_uid, to_uid=uid,
                        edge_type=edge_type, project_id=self.graph_id,
                    )
                except Exception:
                    pass

    # ═══════════════════════════════════════
    # Inter-round decay + post-simulation distillation + anomaly detection + decision recording
    # ═══════════════════════════════════════

    def decay_round(self, round_num: int):
        """
        Inter-round salience decay -- natural forgetting of simulation memory

        DISABLED: decay() is a global operation that degrades salience across
        the entire MindGraph graph (book knowledge + simulation data), not
        just simulation-created nodes. This harms retrieval quality for the
        graph context provider. Simulations are short-lived; natural recency
        bias in retrieval handles salience implicitly.
        """
        logger.debug(f"Skipping inter-round decay (global operation disabled): round={round_num}")

    def record_round_end(self, round_num: int, platform: str,
                         actions_count: int = 0):
        """Record round end as Observation node (via batch API)"""
        display_name = self._get_platform_display_name(platform)
        content = f"Round {round_num}{display_name} simulation completed, {actions_count} actions"
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
            logger.debug(f"Failed to record round observation: {e}")

    def _check_anomaly(self, activity: AgentActivity, content: str):
        """
        Detect if agent behavior contradicts its configured stance

        Simple heuristic: if opposing agent posts positive content or supportive agent posts negative content
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
                        f"posted content inconsistent with stance: {content[:100]}"
                    ),
                    project_id=self.graph_id,
                    severity="medium",
                    agent_name=activity.agent_name,
                )
                logger.info(f"Behavioral anomaly detected: {activity.agent_name} ({stance})")
                # Link Agent -> Anomaly
                agent_uid = self._agent_node_uids.get(activity.agent_name)
                anomaly_uid = anomaly_result.get("uid", "") if isinstance(anomaly_result, dict) else ""
                if agent_uid and anomaly_uid:
                    self._link_agent_to_nodes(agent_uid, [anomaly_uid], "EXHIBITED")
            except Exception as e:
                logger.debug(f"Failed to record anomaly: {e}")

    def _record_decision(self, activity: AgentActivity, content: str) -> bool:
        """
        Record high-impact action as Decision node

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
                logger.debug(f"Failed to record decision: {e}")
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
                logger.debug(f"Failed to record social decision: {e}")
                return False

        return False

    def _distill_simulation(self):
        """
        Post-simulation distillation -- create Summary node + detect emergent patterns

        Called before closing session in stop().
        """
        if not self._created_epistemic_uids:
            logger.info("No Claim nodes, skipping distillation")
            return

        # Distillation: summarize all Claims into Summary
        try:
            self.client.distill(
                label=f"Simulation Summary: {self.graph_id}",
                source_uids=self._created_epistemic_uids[:50],  # Limit UID count
                project_id=self.graph_id,
                content=f"Based on {len(self._created_epistemic_uids)} agent claims auto-distillation summary",
            )
            logger.info(f"Simulation distillation completed: {len(self._created_epistemic_uids)} claims → summary")
        except Exception as e:
            logger.warning(f"Simulation distillation failed: {e}")

        # Simple pattern detection
        if self._total_claims > 5:
            # High uncertainty pattern: most claims have low confidence
            # (Need to track confidence, using total count for rough estimate)
            try:
                self.client.record_pattern(
                    name="Simulation activity pattern",
                    description=(
                        f"This simulation produced {self._total_claims} structured claims, "
                        f"{self._total_traces} social traces, "
                        f"{self._total_activities} total activities"
                    ),
                    project_id=self.graph_id,
                    instance_count=self._total_claims,
                )
            except Exception as e:
                logger.debug(f"Failed to record pattern: {e}")

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
    """Manage graph memory updaters for multiple simulations"""

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
                f"CreatingGraph memory updater: simulation_id={simulation_id}, "
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
                logger.info(f"Stopped graph memory updater: simulation_id={simulation_id}")

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
                        logger.error(f"Failed to stop updater: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("Stopped all graph memory updaters")

    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        return {
            sim_id: updater.get_stats()
            for sim_id, updater in cls._updaters.items()
        }
