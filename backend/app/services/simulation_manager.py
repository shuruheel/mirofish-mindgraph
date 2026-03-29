"""
OASIS模拟管理器
管理Twitter和Reddit双平台并行模拟
使用预设脚本 + LLM智能生成配置参数
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .entity_reader import EntityReader, FilteredEntities
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters

logger = get_logger('mirofish.simulation')


class SimulationStatus(str, Enum):
    """模拟状态"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # 模拟被手动停止
    COMPLETED = "completed"  # 模拟自然完成
    FAILED = "failed"


class PlatformType(str, Enum):
    """平台类型"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """模拟状态"""
    simulation_id: str
    project_id: str
    graph_id: str
    
    # 平台启用状态
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # 状态
    status: SimulationStatus = SimulationStatus.CREATED
    
    # 准备阶段数据
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    
    # 配置生成信息
    config_generated: bool = False
    config_reasoning: str = ""
    
    # 运行时数据
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # 错误信息
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """完整状态字典（内部使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def to_simple_dict(self) -> Dict[str, Any]:
        """简化状态字典（API返回使用）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    模拟管理器
    
    核心功能：
    1. 从MindGraph图谱读取实体并过滤
    2. 生成OASIS Agent Profile
    3. 使用LLM智能生成模拟配置参数
    4. 准备预设脚本所需的所有文件
    """
    
    # 模拟数据存储目录
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # 确保目录存在
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # 内存中的模拟状态缓存
        self._simulations: Dict[str, SimulationState] = {}
    
    def _sync_entities_with_profiles(
        self, entities: List, sim_dir: str
    ) -> List:
        """
        Deduplicate profiles on disk, then reorder entities to match.

        OASIS maps agents by index: agent_id=0 gets profile row 0, etc.
        The config generator must produce agent_configs in the same order
        so that agent_id N's config matches profile row N's persona/bio.

        Steps:
        1. Load profiles from disk
        2. Deduplicate profiles (keep first occurrence of each name)
        3. Write deduplicated profiles back to disk
        4. Reorder entities to match the deduplicated profile order

        Returns the entity list in profile order, or the original list
        if no profiles exist.
        """
        import csv as csv_mod

        reddit_path = os.path.join(sim_dir, "reddit_profiles.json")
        twitter_path = os.path.join(sim_dir, "twitter_profiles.csv")

        # --- Step 1+2: Load and deduplicate profiles ---
        profile_names = []

        if os.path.exists(reddit_path):
            try:
                with open(reddit_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                seen = set()
                deduped = []
                for p in profiles:
                    name = p.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        deduped.append(p)
                if len(deduped) < len(profiles):
                    logger.info(f"Reddit profiles去重: {len(profiles)} → {len(deduped)}")
                    # Rewrite user_ids to be sequential
                    for i, p in enumerate(deduped):
                        p["user_id"] = i
                    with open(reddit_path, 'w', encoding='utf-8') as f:
                        json.dump(deduped, f, ensure_ascii=False, indent=2)
                profile_names = [p.get("name", "") for p in deduped]
            except Exception as e:
                logger.warning(f"加载Reddit profiles失败: {e}")

        if os.path.exists(twitter_path):
            try:
                with open(twitter_path, 'r', encoding='utf-8') as f:
                    reader = csv_mod.DictReader(f)
                    rows = list(reader)
                    fieldnames = reader.fieldnames
                seen = set()
                deduped_rows = []
                for row in rows:
                    name = row.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        deduped_rows.append(row)
                if len(deduped_rows) < len(rows):
                    logger.info(f"Twitter profiles去重: {len(rows)} → {len(deduped_rows)}")
                    # Rewrite user_ids to be sequential
                    for i, row in enumerate(deduped_rows):
                        row["user_id"] = str(i)
                    with open(twitter_path, 'w', encoding='utf-8', newline='') as f:
                        writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(deduped_rows)
                if not profile_names:  # Only use twitter if reddit didn't set names
                    profile_names = [r.get("name", "") for r in deduped_rows]
            except Exception as e:
                logger.warning(f"加载Twitter profiles失败: {e}")

        if not profile_names:
            return entities

        # --- Step 3: Build name→entity lookup ---
        entity_by_name = {}
        for entity in entities:
            if entity.name not in entity_by_name:
                entity_by_name[entity.name] = entity

        # --- Step 4: Reorder entities to match profile order ---
        # ONLY include entities that have a matching profile.
        # Entities without profiles would cause OASIS to hang (no persona).
        reordered = []
        seen_names = set()
        skipped = []
        for name in profile_names:
            if name in seen_names:
                continue
            seen_names.add(name)
            entity = entity_by_name.get(name)
            if entity:
                reordered.append(entity)
            else:
                logger.warning(f"Profile '{name}' 无匹配实体")

        # Log entities that have no profile (will NOT be included)
        for entity in entities:
            if entity.name not in seen_names:
                skipped.append(entity.name)
        if skipped:
            logger.info(f"跳过 {len(skipped)} 个无Profile的实体: {skipped[:5]}...")

        logger.info(
            f"实体同步: {len(entities)} entities → {len(reordered)} "
            f"(profiles: {len(profile_names)}, skipped: {len(skipped)})"
        )
        return reordered

    def _get_simulation_dir(self, simulation_id: str) -> str:
        """获取模拟数据目录"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """保存模拟状态到文件"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """从文件加载模拟状态"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        创建新的模拟
        
        Args:
            project_id: 项目ID
            graph_id: MindGraph图谱ID
            enable_twitter: 是否启用Twitter模拟
            enable_reddit: 是否启用Reddit模拟
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"创建模拟: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 20,
        max_agents: int = 0,
        source: str = "upload"
    ) -> SimulationState:
        """
        准备模拟环境（全程自动化）
        
        步骤：
        1. 从MindGraph图谱读取并过滤实体
        2. 为每个实体生成OASIS Agent Profile（可选LLM增强，支持并行）
        3. 使用LLM智能生成模拟配置参数（时间、活跃度、发言频率等）
        4. 保存配置文件和Profile文件
        5. 复制预设脚本到模拟目录
        
        Args:
            simulation_id: 模拟ID
            simulation_requirement: 模拟需求描述（用于LLM生成配置）
            document_text: 原始文档内容（用于LLM理解背景）
            defined_entity_types: 预定义的实体类型（可选）
            use_llm_for_profiles: 是否使用LLM生成详细人设
            progress_callback: 进度回调函数 (stage, progress, message)
            parallel_profile_count: 并行生成人设的数量，默认3
            
        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== 阶段1: 读取并过滤实体 ==========
            if progress_callback:
                progress_callback("reading", 0, "正在连接知识图谱...")
            
            reader = EntityReader()
            
            if progress_callback:
                progress_callback("reading", 30, "正在读取节点数据...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True,
                max_entities=max_agents,
                source=source
            )
            
            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            
            if progress_callback:
                progress_callback(
                    "reading", 100, 
                    f"完成，共 {filtered.filtered_count} 个实体",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "没有找到符合条件的实体，请检查图谱是否正确构建"
                self._save_simulation_state(state)
                return state
            
            # ========== 阶段2: 生成Agent Profile ==========
            total_entities = len(filtered.entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0, 
                    "开始生成...",
                    current=0,
                    total=total_entities
                )
            
            # 传入graph_id以启用MindGraph检索功能，获取更丰富的上下文
            generator = OasisProfileGenerator(graph_id=state.graph_id, source=source)
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # 设置实时保存的文件路径（优先使用 Reddit JSON 格式）
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=filtered.entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # 传入graph_id用于MindGraph检索
                parallel_count=parallel_profile_count,  # 并行生成数量
                realtime_output_path=realtime_output_path,  # 实时保存路径
                output_platform=realtime_platform  # 输出格式
            )
            
            state.profiles_count = len(profiles)
            
            # 保存Profile文件（注意：Twitter使用CSV格式，Reddit使用JSON格式）
            # Reddit 已经在生成过程中实时保存了，这里再保存一次确保完整性
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "保存Profile文件...",
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter使用CSV格式！这是OASIS的要求
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"完成，共 {len(profiles)} 个Profile",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== 阶段3: LLM智能生成模拟配置（支持断点续传） ==========
            config_path = os.path.join(sim_dir, "simulation_config.json")

            if os.path.exists(config_path) and os.path.getsize(config_path) > 100:
                # Config already generated (previous run completed phase 3) — skip
                logger.info("配置文件已存在，跳过配置生成阶段")
                if progress_callback:
                    progress_callback("generating_config", 100, "配置已存在，跳过",
                                      current=1, total=1)
                with open(config_path, 'r', encoding='utf-8') as f:
                    import json as _json
                    config_data = _json.load(f)
                # Reconstruct SimulationParameters minimally for phase 4
                from .simulation_config_generator import SimulationParameters, AgentActivityConfig
                sim_params = SimulationParameters(
                    simulation_id=simulation_id,
                    project_id=state.project_id,
                    graph_id=state.graph_id,
                    simulation_requirement=simulation_requirement,
                    generation_reasoning=config_data.get("generation_reasoning", ""),
                )
                sim_params.agent_configs = [
                    AgentActivityConfig(**ac) for ac in config_data.get("agent_configs", [])
                ]
            else:
                # Sync entity order with existing profiles to ensure config agent_ids
                # match profile user_ids (OASIS maps by index, not by name)
                config_entities = self._sync_entities_with_profiles(
                    filtered.entities, sim_dir
                )

                num_agent_batches = (len(config_entities) + 14) // 15  # ceil(n/15)
                total_config_steps = 3 + num_agent_batches

                def config_progress(step, total, msg):
                    if progress_callback:
                        progress_callback("generating_config",
                                          int(step / total * 100), msg,
                                          current=step, total=total)

                config_generator = SimulationConfigGenerator()
                sim_params = config_generator.generate_config(
                    simulation_id=simulation_id,
                    project_id=state.project_id,
                    graph_id=state.graph_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    entities=config_entities,
                    enable_twitter=state.enable_twitter,
                    enable_reddit=state.enable_reddit,
                    progress_callback=config_progress,
                    checkpoint_dir=sim_dir,
                )

                # 保存配置文件
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(sim_params.to_json())

                if progress_callback:
                    progress_callback("generating_config", 100, "配置生成完成",
                                      current=1, total=1)

            state.config_generated = True
            state.config_reasoning = getattr(sim_params, 'generation_reasoning', '')
            self._save_simulation_state(state)

            # ========== 阶段4: 注册认知结构 + Agent节点到MindGraph ==========
            # Idempotent: skip if agent_node_uids.json already exists from a previous run
            uids_path = os.path.join(sim_dir, "agent_node_uids.json")
            if Config.MINDGRAPH_API_KEY and not os.path.exists(uids_path):
                try:
                    from ..utils.mindgraph_client import MindGraphClient
                    mg_client = MindGraphClient()

                    # 注册预测问题为Hypothesis节点
                    mg_client.add_hypothesis(
                        statement=simulation_requirement,
                        project_id=state.graph_id,
                    )
                    logger.info(f"已注册预测假说: {simulation_requirement[:50]}...")

                    # 使用 find_or_create_entity 确保幂等（避免重试时重复创建）
                    agent_node_uids = {}  # agent_name → agent_node_uid
                    journals_created = 0

                    # Batch: collect all agent data first, then create nodes
                    for agent_config in sim_params.agent_configs:
                        name = agent_config.entity_name
                        stance = getattr(agent_config, 'stance', 'neutral')
                        sentiment = getattr(agent_config, 'sentiment_bias', 0.0)
                        influence = getattr(agent_config, 'influence_weight', 1.0)
                        entity_type = getattr(agent_config, 'entity_type', '')

                        # find_or_create_entity is idempotent — returns existing node if name matches
                        try:
                            result = mg_client.create_entity(
                                name=name,
                                entity_type="SimulationAgent",
                                project_id=state.graph_id,
                                description=f"{entity_type}: {stance} stance, sentiment={sentiment}",
                                props={
                                    "entity_type": entity_type,
                                    "stance": stance,
                                    "sentiment_bias": sentiment,
                                    "influence_weight": influence,
                                    "simulation_id": simulation_id,
                                },
                            )
                            uid = result.get("uid", "")
                            if uid:
                                agent_node_uids[name] = uid
                        except Exception as e:
                            logger.warning(f"注册Agent节点失败 ({name}): {e}")

                        # 为非中立Agent创建Journal条目记录立场（Memory层）
                        if stance != "neutral":
                            try:
                                journal_content = (
                                    f"{name} holds a {stance} stance "
                                    f"with sentiment bias {sentiment:.2f} "
                                    f"and influence weight {influence:.2f}"
                                )
                                journal_result = mg_client.create_journal(
                                    content=journal_content,
                                    project_id=state.graph_id,
                                    journal_type="stance",
                                    tags=[stance, entity_type],
                                )
                                journals_created += 1
                                # 链接Agent → Journal
                                journal_uid = journal_result.get("uid", "")
                                agent_uid = agent_node_uids.get(name, "")
                                if journal_uid and agent_uid:
                                    mg_client.add_link(
                                        from_uid=agent_uid,
                                        to_uid=journal_uid,
                                        edge_type="HAS_JOURNAL",
                                        project_id=state.graph_id,
                                    )
                            except Exception as e:
                                logger.warning(f"创建Journal条目失败 ({name}): {e}")

                    logger.info(
                        f"已注册 {len(agent_node_uids)} 个Agent节点, "
                        f"{journals_created} 个Journal条目"
                    )

                    # 保存agent_node_uids映射到模拟目录
                    if agent_node_uids:
                        import json as _json
                        with open(uids_path, 'w', encoding='utf-8') as f:
                            _json.dump(agent_node_uids, f, ensure_ascii=False)
                        logger.info(f"Agent节点映射已保存: {uids_path}")

                except Exception as e:
                    logger.warning(f"注册认知结构失败（不影响模拟）: {e}")
            elif os.path.exists(uids_path):
                logger.info(f"Agent节点映射已存在，跳过认知结构注册: {uids_path}")

            # 注意：运行脚本保留在 backend/scripts/ 目录，不再复制到模拟目录
            # 启动模拟时，simulation_runner 会从 scripts/ 目录运行脚本

            # 更新状态
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"模拟准备完成: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"模拟准备失败: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """获取模拟状态"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """列出所有模拟"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # 跳过隐藏文件（如 .DS_Store）和非目录文件
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """获取模拟的Agent Profile"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"模拟不存在: {simulation_id}")
        
        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")
        
        if not os.path.exists(profile_path):
            return []
        
        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """获取模拟配置"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """获取运行说明"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        return {
            "simulation_dir": sim_dir,
            "scripts_dir": scripts_dir,
            "config_file": config_path,
            "commands": {
                "twitter": f"python {scripts_dir}/run_twitter_simulation.py --config {config_path}",
                "reddit": f"python {scripts_dir}/run_reddit_simulation.py --config {config_path}",
                "parallel": f"python {scripts_dir}/run_parallel_simulation.py --config {config_path}",
            },
            "instructions": (
                f"1. 激活conda环境: conda activate MiroFish\n"
                f"2. 运行模拟 (脚本位于 {scripts_dir}):\n"
                f"   - 单独运行Twitter: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - 单独运行Reddit: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - 并行运行双平台: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
