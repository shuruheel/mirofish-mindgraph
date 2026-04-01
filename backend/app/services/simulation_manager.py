"""
OASIS Simulation Manager
Manages Twitter and Reddit dual-platform parallel simulation
Uses preset scripts + LLM-powered configuration parameter generation
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
    """Simulation status"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"      # Simulation manually stopped
    COMPLETED = "completed"  # Simulation completed naturally
    FAILED = "failed"


class PlatformType(str, Enum):
    """Platform type"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """Simulation state"""
    simulation_id: str
    project_id: str
    graph_id: str

    # Platform enabled status
    enable_twitter: bool = True
    enable_reddit: bool = True

    # Status
    status: SimulationStatus = SimulationStatus.CREATED

    # Preparation phase data
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)

    # Config generation info
    config_generated: bool = False
    config_reasoning: str = ""

    # Runtime data
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Error info
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Full state dict (internal use)"""
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
        """Simplified state dict (for API responses)"""
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
    Simulation Manager

    Core features:
    1. Read and filter entities from MindGraph knowledge graph
    2. Generate OASIS Agent Profiles
    3. Use LLM to intelligently generate simulation configuration parameters
    4. Prepare all files required by preset scripts
    """
    
    # Simulation data storage directory
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # Ensure directory exists
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)

        # In-memory simulation state cache
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
                    logger.info(f"Reddit profiles deduplicated: {len(profiles)} → {len(deduped)}")
                    # Rewrite user_ids to be sequential
                    for i, p in enumerate(deduped):
                        p["user_id"] = i
                    with open(reddit_path, 'w', encoding='utf-8') as f:
                        json.dump(deduped, f, ensure_ascii=False, indent=2)
                profile_names = [p.get("name", "") for p in deduped]
            except Exception as e:
                logger.warning(f"Failed to load Reddit profiles: {e}")

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
                    logger.info(f"Twitter profiles deduplicated: {len(rows)} → {len(deduped_rows)}")
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
                logger.warning(f"Failed to load Twitter profiles: {e}")

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
                logger.warning(f"Profile '{name}' has no matching entity")

        # Log entities that have no profile (will NOT be included)
        for entity in entities:
            if entity.name not in seen_names:
                skipped.append(entity.name)
        if skipped:
            logger.info(f"Skipped {len(skipped)} entities without profiles: {skipped[:5]}...")

        logger.info(
            f"Entity sync: {len(entities)} entities → {len(reordered)} "
            f"(profiles: {len(profile_names)}, skipped: {len(skipped)})"
        )
        return reordered

    def _get_simulation_dir(self, simulation_id: str) -> str:
        """Get simulation data directory"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """Save simulation state to file"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """Load simulation state from file"""
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
        Create a new simulation

        Args:
            project_id: Project ID
            graph_id: MindGraph knowledge graph ID
            enable_twitter: Whether to enable Twitter simulation
            enable_reddit: Whether to enable Reddit simulation

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
        logger.info(f"Created simulation: {simulation_id}, project={project_id}, graph={graph_id}")
        
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
        Prepare simulation environment (fully automated)

        Steps:
        1. Read and filter entities from MindGraph knowledge graph
        2. Generate OASIS Agent Profile for each entity (optional LLM enhancement, parallel support)
        3. Use LLM to intelligently generate simulation config parameters (time, activity, post frequency, etc.)
        4. Save config files and profile files
        5. Copy preset scripts to simulation directory

        Args:
            simulation_id: Simulation ID
            simulation_requirement: Simulation requirement description (for LLM config generation)
            document_text: Original document content (for LLM context understanding)
            defined_entity_types: Predefined entity types (optional)
            use_llm_for_profiles: Whether to use LLM for detailed persona generation
            progress_callback: Progress callback function (stage, progress, message)
            parallel_profile_count: Number of personas to generate in parallel, default 3

        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"Simulation not found: {simulation_id}")

        try:
            state.status = SimulationStatus.PREPARING
            self._save_simulation_state(state)

            sim_dir = self._get_simulation_dir(simulation_id)

            # ========== Phase 1: Read and filter entities ==========
            if progress_callback:
                progress_callback("reading", 0, "Connecting to knowledge graph...")
            
            reader = EntityReader()
            
            if progress_callback:
                progress_callback("reading", 30, "Reading node data...")
            
            filtered = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=defined_entity_types,
                enrich_with_edges=True,
                max_entities=max_agents,
                simulation_requirement=simulation_requirement,
                source=source
            )
            
            state.entities_count = filtered.filtered_count
            state.entity_types = list(filtered.entity_types)
            
            if progress_callback:
                progress_callback(
                    "reading", 100,
                    f"Complete, {filtered.filtered_count} entities found",
                    current=filtered.filtered_count,
                    total=filtered.filtered_count
                )
            
            if filtered.filtered_count == 0:
                state.status = SimulationStatus.FAILED
                state.error = "No qualifying entities found. Please check if the knowledge graph was built correctly."
                self._save_simulation_state(state)
                return state
            
            # ========== Phase 2: Generate Agent Profiles ==========
            total_entities = len(filtered.entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0,
                    "Starting generation...",
                    current=0,
                    total=total_entities
                )
            
            # Pass graph_id to enable MindGraph retrieval for richer context
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
            
            # Set real-time save file path (prefer Reddit JSON format)
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
                graph_id=state.graph_id,  # Pass graph_id for MindGraph retrieval
                parallel_count=parallel_profile_count,  # Parallel generation count
                realtime_output_path=realtime_output_path,  # Real-time save path
                output_platform=realtime_platform  # Output format
            )
            
            state.profiles_count = len(profiles)
            
            # Save profile files (note: Twitter uses CSV format, Reddit uses JSON format)
            # Reddit was already saved in real-time during generation; save again here to ensure completeness
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95, 
                    "Saving profile files...",
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
                # Twitter uses CSV format! This is an OASIS requirement
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100, 
                    f"Complete, {len(profiles)} profiles generated",
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== Phase 3: LLM-powered simulation config generation (supports checkpoint resume) ==========
            config_path = os.path.join(sim_dir, "simulation_config.json")

            if os.path.exists(config_path) and os.path.getsize(config_path) > 100:
                # Config already generated (previous run completed phase 3) — skip
                logger.info("Config file already exists, skipping config generation phase")
                if progress_callback:
                    progress_callback("generating_config", 100, "Config already exists, skipping",
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

                # Save config file
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(sim_params.to_json())

                if progress_callback:
                    progress_callback("generating_config", 100, "Config generation complete",
                                      current=1, total=1)

            state.config_generated = True
            state.config_reasoning = getattr(sim_params, 'generation_reasoning', '')
            self._save_simulation_state(state)

            # ========== Phase 4: Register cognitive structures + Agent nodes to MindGraph ==========
            # Idempotent: skip if agent_node_uids.json already exists from a previous run
            # Uses batch API to minimize write pressure on CozoDB's single writer
            uids_path = os.path.join(sim_dir, "agent_node_uids.json")
            if Config.MINDGRAPH_API_KEY and not os.path.exists(uids_path):
                try:
                    from ..utils.mindgraph_client import MindGraphClient
                    mg_client = MindGraphClient()

                    # Register prediction question as Hypothesis node
                    mg_client.add_hypothesis(
                        statement=simulation_requirement,
                        project_id=state.graph_id,
                    )
                    logger.info(f"Registered prediction hypothesis: {simulation_requirement[:50]}...")

                    # ── Collect all nodes for batch creation ──
                    # Agent nodes (simulation agents) + Journal nodes (stance records)
                    batch_nodes = []
                    node_metadata = []  # Parallel array: {kind, name, ...}

                    for agent_config in sim_params.agent_configs:
                        name = agent_config.entity_name
                        stance = getattr(agent_config, 'stance', 'neutral')
                        sentiment = getattr(agent_config, 'sentiment_bias', 0.0)
                        influence = getattr(agent_config, 'influence_weight', 1.0)
                        entity_type = getattr(agent_config, 'entity_type', '')

                        # Agent node for this simulation agent
                        batch_nodes.append({
                            "label": name,
                            "props": {
                                "_type": "Agent",
                                "original_entity_type": entity_type,
                                "stance": stance,
                                "sentiment_bias": sentiment,
                                "influence_weight": influence,
                                "simulation_id": simulation_id,
                                "summary": f"{entity_type}: {stance} stance, sentiment={sentiment}",
                            },
                            "agent_id": state.graph_id,
                        })
                        node_metadata.append({"kind": "agent", "name": name})

                        # Journal node for non-neutral agents
                        if stance != "neutral":
                            journal_content = (
                                f"{name} holds a {stance} stance "
                                f"with sentiment bias {sentiment:.2f} "
                                f"and influence weight {influence:.2f}"
                            )
                            batch_nodes.append({
                                "label": journal_content[:100],
                                "props": {
                                    "_type": "Journal",
                                    "content": journal_content,
                                    "journal_type": "stance",
                                    "tags": [stance, entity_type],
                                },
                                "agent_id": state.graph_id,
                            })
                            node_metadata.append({"kind": "journal", "name": name})

                    # ── Single batch_create for all nodes ──
                    agent_node_uids = {}
                    journals_created = 0
                    node_uids = []

                    if batch_nodes:
                        result = mg_client.batch_create(nodes=batch_nodes)
                        node_uids = result.get("node_uids", []) if isinstance(result, dict) else []
                        logger.info(f"Batch created {len(node_uids)} nodes ({len(batch_nodes)} requested)")

                    # ── Parse UIDs and build edges ──
                    batch_edges = []
                    last_entity_uid = ""

                    for i, meta in enumerate(node_metadata):
                        uid = node_uids[i] if i < len(node_uids) else ""
                        if not uid:
                            continue

                        if meta["kind"] == "agent":
                            agent_node_uids[meta["name"]] = uid
                            last_entity_uid = uid

                        elif meta["kind"] == "journal":
                            journals_created += 1
                            # Link Agent → Journal
                            entity_uid = agent_node_uids.get(meta["name"], "")
                            if entity_uid:
                                batch_edges.append({
                                    "from_uid": entity_uid,
                                    "to_uid": uid,
                                    "edge_type": "HAS_JOURNAL",
                                })

                    # ── Single batch_create for all edges ──
                    if batch_edges:
                        try:
                            mg_client.batch_create(edges=batch_edges)
                        except Exception as e:
                            logger.warning(f"Batch HAS_JOURNAL edge creation failed: {e}")

                    logger.info(
                        f"Registered {len(agent_node_uids)} Agent nodes, "
                        f"{journals_created} Journal entries "
                        f"(2 API calls instead of ~{len(agent_node_uids) + journals_created * 2})"
                    )

                    # Save agent_node_uids mapping to simulation directory
                    if agent_node_uids:
                        import json as _json
                        with open(uids_path, 'w', encoding='utf-8') as f:
                            _json.dump(agent_node_uids, f, ensure_ascii=False)
                        logger.info(f"Agent node mapping saved: {uids_path}")

                except Exception as e:
                    logger.warning(f"Failed to register cognitive structures (does not affect simulation): {e}")
            elif os.path.exists(uids_path):
                logger.info(f"Agent node mapping already exists, skipping cognitive structure registration: {uids_path}")

            # Note: run scripts remain in backend/scripts/ directory, no longer copied to simulation directory
            # When starting simulation, simulation_runner runs scripts from the scripts/ directory

            # Update status
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"Simulation preparation complete: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"Simulation preparation failed: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """Get simulation state"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """List all simulations"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # Skip hidden files (e.g. .DS_Store) and non-directory files
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_profiles(self, simulation_id: str, platform: str = "reddit") -> List[Dict[str, Any]]:
        """Get simulation Agent Profiles"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"Simulation not found: {simulation_id}")

        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(sim_dir, f"{platform}_profiles.json")
        
        if not os.path.exists(profile_path):
            return []
        
        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """Get simulation config"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, str]:
        """Get run instructions"""
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
                f"1. Activate conda environment: conda activate MiroFish\n"
                f"2. Run simulation (scripts located in {scripts_dir}):\n"
                f"   - Run Twitter only: python {scripts_dir}/run_twitter_simulation.py --config {config_path}\n"
                f"   - Run Reddit only: python {scripts_dir}/run_reddit_simulation.py --config {config_path}\n"
                f"   - Run both platforms in parallel: python {scripts_dir}/run_parallel_simulation.py --config {config_path}"
            )
        }
