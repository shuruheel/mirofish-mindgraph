# MiroFish Current Architecture

**Date:** 2026-03-21
**Source:** Codebase inspection of every backend service, API route, model, utility, frontend API client, and router.

---

## 1. System Overview

MiroFish is a multi-agent social simulation engine for predictive analytics. Users upload documents, a knowledge graph is constructed via Zep Cloud, LLM-generated personas populate a simulated social world, OASIS (CAMEL-AI) runs parallel Twitter/Reddit simulations, and a ReportAgent generates prediction reports by querying the post-simulation graph.

**Tech stack:** Flask (backend), Vue 3 + Vite (frontend), Zep Cloud (knowledge graph + temporal memory), OASIS/CAMEL-AI (simulation engine), OpenAI SDK (LLM calls to any compatible API).

**No database.** All persistence is file-based: JSON files on disk for projects, simulations, tasks, reports, and run states.

---

## 2. Five-Stage Pipeline

### Stage 1: Graph Construction

**Flow:** File upload → Text extraction → LLM ontology generation → Zep graph creation → Text chunking → Batch episode ingestion → Wait for Zep processing

**Key files:**
- `backend/app/api/graph.py` — Two main endpoints: `POST /ontology/generate` (sync, returns ontology) and `POST /build` (async, starts background thread)
- `backend/app/services/ontology_generator.py` — LLM-driven ontology design. Prompts the LLM to produce exactly 10 entity types (8 domain-specific + Person/Organization fallbacks) and 6-10 edge types. Output is a JSON schema with entity_types, edge_types, and analysis_summary.
- `backend/app/services/graph_builder.py` — Core Zep integration. Creates a Zep Standalone Graph, dynamically builds Pydantic entity/edge classes from the ontology JSON, calls `client.graph.set_ontology()`, then batch-ingests text chunks as `EpisodeData`. Polls each episode's `processed` status with 3-second intervals and 10-minute timeout.
- `backend/app/services/text_processor.py` — Simple text chunking (fixed window + overlap). Delegates to `file_parser.py` for PDF/MD/TXT extraction.
- `backend/app/utils/zep_paging.py` — Handles Zep's UUID-cursor pagination for nodes and edges with exponential-backoff retry.

**Ontology schema:** Each entity type has a name (PascalCase), description (<100 chars), attributes (1-3, with reserved name avoidance: uuid, name, group_id, created_at, summary), and examples. Edge types have source_targets linking entity type pairs.

**Known fragility:** The dynamic Pydantic class generation in `set_ontology()` (lines 199-286 of graph_builder.py) is complex — it creates classes at runtime with `type()`, handles reserved attribute names, and must match Zep SDK expectations exactly. This is the area most likely to fail during graph construction.

### Stage 2: Environment Setup

**Flow:** Read entities from Zep graph → Filter to typed entities → Generate OASIS agent profiles (LLM-enhanced) → Generate simulation config (LLM) → Save files to simulation directory

**Key files:**
- `backend/app/api/simulation.py` — `POST /create` (creates simulation record), `POST /prepare` (async, starts background thread for full preparation pipeline)
- `backend/app/services/zep_entity_reader.py` — Reads all nodes from Zep, filters to nodes with custom labels (not just "Entity"/"Node"), enriches each entity with related edges and neighbor nodes. Returns `FilteredEntities` with entity type distribution.
- `backend/app/services/oasis_profile_generator.py` — Converts graph entities to OASIS agent profiles. For each entity: queries Zep graph search (edges + nodes, parallel) for enriched context, then calls LLM to generate detailed persona (bio, persona narrative, age, gender, MBTI, profession, interests, social stats). Distinguishes individual vs. group entity types. Supports parallel generation and real-time file output.
- `backend/app/services/simulation_config_generator.py` — LLM-driven simulation parameter generation. Multi-step: (1) time config (total hours, minutes/round, activity patterns based on China timezone), (2) event config (initial posts, hot topics, narrative direction), (3) per-agent configs in batches of 15 (activity level, posting frequency, sentiment bias, stance, influence weight), (4) platform configs. Assigns initial posts to appropriate agents.
- `backend/app/services/simulation_manager.py` — State machine: CREATED → PREPARING → READY → RUNNING → PAUSED → STOPPED/COMPLETED/FAILED. Persists state as `state.json` in simulation directory. Also holds in-memory cache.

**Output files per simulation:**
- `state.json` — Simulation metadata and status
- `reddit_profiles.json` — Agent profiles in Reddit format
- `twitter_profiles.csv` — Agent profiles in Twitter/CSV format
- `simulation_config.json` — Full simulation parameters

### Stage 3: Simulation

**Flow:** Launch OASIS script as subprocess → Monitor action logs → Optionally update Zep graph with agent activities

**Key files:**
- `backend/app/services/simulation_runner.py` — Launches `run_parallel_simulation.py` (or platform-specific variants) as a subprocess. Creates a monitoring thread that tails `{platform}/actions.jsonl` files, parses agent actions, updates `SimulationRunState`, and optionally feeds activities to `ZepGraphMemoryUpdater`. Manages process lifecycle, cleanup on server shutdown via `atexit`.
- `backend/app/services/simulation_ipc.py` — File-system based IPC between Flask and the running OASIS process. Flask writes command JSON files to `ipc_commands/`, simulation script polls and writes responses to `ipc_responses/`. Supports INTERVIEW, BATCH_INTERVIEW, and CLOSE_ENV commands.
- `backend/app/services/zep_graph_memory_updater.py` — Background thread that buffers agent activities per platform, batch-sends them to Zep as text episodes (batch size: 5). Converts each action to natural language (e.g., "张三: 发布了一条帖子：「内容」"). Handles retry with exponential backoff. Managed by `ZepGraphMemoryManager` singleton that tracks updaters per simulation.
- `backend/scripts/run_parallel_simulation.py`, `run_twitter_simulation.py`, `run_reddit_simulation.py` — OASIS entry points (not deeply inspected as they depend on the external OASIS library).

**Action types tracked:** CREATE_POST, LIKE_POST, DISLIKE_POST, REPOST, QUOTE_POST, FOLLOW, CREATE_COMMENT, LIKE_COMMENT, DISLIKE_COMMENT, SEARCH_POSTS, SEARCH_USER, MUTE, DO_NOTHING (skipped in memory updates).

**Memory write path during simulation:**
1. OASIS script writes `actions.jsonl` (one JSON object per agent action per line)
2. `SimulationRunner._monitor_simulation()` reads new lines every 2 seconds
3. If `enable_graph_memory_update=True`, each action is converted to `AgentActivity` and enqueued in `ZepGraphMemoryUpdater`
4. Updater batches 5 activities per platform, converts to natural language, calls `client.graph.add(type="text", data=combined_text)` on the same graph_id used for seed material
5. Zep processes the text episodes asynchronously, extracting entities and relationships

**Critical observation:** Simulation activities are written to the **same Zep graph** as the seed material. There is no separation between "world knowledge" and "simulation memory." This means the graph evolves during simulation, mixing source facts with agent-generated content.

### Stage 4: Report Generation

**Flow:** ReportAgent plans outline → For each section: ReACT loop (think → tool call → observe → repeat) → Final markdown assembly

**Key files:**
- `backend/app/api/report.py` — `POST /generate` (async, starts background thread), `POST /chat` (synchronous conversation with ReportAgent), plus section-streaming and log endpoints.
- `backend/app/services/report_agent.py` — The ReportAgent implements a custom ReACT (Reasoning + Acting) loop:
  1. **Planning phase:** Queries Zep for graph statistics and sample facts, sends to LLM to generate a 2-5 section outline
  2. **Section generation:** For each section, enters a multi-turn loop where the LLM can call tools (max 5 calls per section, max 2 reflection rounds), then outputs "Final Answer:" with section content
  3. **Tool dispatch:** Parses `<tool_call>` XML from LLM output, executes the matching tool, injects result as "Observation"
  4. **Assembly:** Concatenates all sections into a single markdown document
- `backend/app/services/zep_tools.py` — Four tools available to the ReportAgent:
  - **InsightForge** — Deep analysis: LLM generates sub-questions, then parallel Zep searches (edges + nodes) for each, plus entity-specific queries. Returns semantic facts, entity insights, and relationship chains.
  - **PanoramaSearch** — Breadth search: fetches all nodes and edges from the graph, categorizes facts as active vs. historical/expired based on Zep's temporal model.
  - **QuickSearch** — Simple Zep edge search with RRF reranking.
  - **InterviewAgents** — Reads agent profiles from disk, uses LLM to select relevant agents and generate questions, sends BATCH_INTERVIEW IPC commands to the running simulation, collects and summarizes responses.

**ReportManager** — File-based report persistence under `uploads/reports/{report_id}/`. Stores `report.json`, `report.md`, per-section markdown files, `agent_log.jsonl` (structured execution trace), and `console_log.txt`.

### Stage 5: Deep Interaction

**Flow:** User sends message → ReportAgent.chat() → LLM with tool access responds

**The same ReportAgent** is instantiated for chat, with the same Zep tools available. Chat history is passed from the frontend. The frontend sends to `POST /api/report/chat` with `simulation_id`, `message`, and `chat_history`.

**Interview optimization:** The `INTERVIEW_PROMPT_PREFIX` is prepended to user prompts during agent interviews to prevent the OASIS agent from calling tools and instead respond directly as their persona.

---

## 3. Where Zep Is Used (Complete Callsite Map)

| Callsite | Module | Operation | Data Direction |
|----------|--------|-----------|----------------|
| Graph creation | `graph_builder.py:create_graph` | `client.graph.create()` | Write |
| Ontology setup | `graph_builder.py:set_ontology` | `client.graph.set_ontology()` | Write |
| Text ingestion | `graph_builder.py:add_text_batches` | `client.graph.add_batch()` | Write |
| Episode polling | `graph_builder.py:_wait_for_episodes` | `client.graph.episode.get()` | Read |
| Node listing | `zep_paging.py:fetch_all_nodes` | `client.graph.node.get_by_graph_id()` | Read |
| Edge listing | `zep_paging.py:fetch_all_edges` | `client.graph.edge.get_by_graph_id()` | Read |
| Node detail | `zep_entity_reader.py` | `client.graph.node.get()` | Read |
| Node edges | `zep_entity_reader.py:get_node_edges` | `client.graph.node.get_entity_edges()` | Read |
| Profile enrichment | `oasis_profile_generator.py` | `client.graph.search(scope="edges"/"nodes")` | Read |
| Simulation memory | `zep_graph_memory_updater.py` | `client.graph.add(type="text")` | Write |
| Report search | `zep_tools.py:search_graph` | `client.graph.search(scope="edges", reranker="rrf")` | Read |
| InsightForge search | `zep_tools.py` | `client.graph.search()` (multiple parallel calls) | Read |
| PanoramaSearch | `zep_tools.py` | `fetch_all_nodes()` + `fetch_all_edges()` | Read |
| Graph deletion | `graph_builder.py:delete_graph` | `client.graph.delete()` | Write |
| Graph statistics | `zep_tools.py:get_graph_statistics` | `fetch_all_nodes()` + `fetch_all_edges()` | Read |

**Zep SDK version:** `zep_cloud` package. Uses `Zep(api_key=...)` client. Imports: `EpisodeData`, `EntityEdgeSourceTarget`, `InternalServerError`, and ontology classes from `zep_cloud.external_clients.ontology`.

---

## 4. Where OASIS Is Used (Integration Boundary)

MiroFish does **not** import OASIS directly in its backend code. OASIS runs as an external subprocess:

1. **Profile generation** — MiroFish generates profile files (JSON/CSV) in OASIS's expected format
2. **Config generation** — MiroFish generates `simulation_config.json` with parameters OASIS scripts consume
3. **Script launching** — `SimulationRunner` spawns `backend/scripts/run_*.py` via `subprocess.Popen`
4. **Log parsing** — MiroFish reads `{platform}/actions.jsonl` files written by OASIS
5. **IPC for interviews** — File-based command/response protocol for agent interaction during/after simulation

MiroFish controls OASIS through its configuration files and the IPC protocol. It does not modify OASIS internals.

---

## 5. State Persistence

### Server-side state (all file-based, no database)

| State Type | Storage Location | Format | Lifetime |
|-----------|-----------------|--------|----------|
| Projects | `uploads/projects/{project_id}/project.json` | JSON | Until deleted |
| Extracted text | `uploads/projects/{project_id}/extracted_text.txt` | Plain text | Until project deleted |
| Uploaded files | `uploads/projects/{project_id}/files/` | Original format | Until project deleted |
| Simulations | `uploads/simulations/{sim_id}/state.json` | JSON | Until deleted |
| Simulation config | `uploads/simulations/{sim_id}/simulation_config.json` | JSON | Per simulation |
| Agent profiles | `uploads/simulations/{sim_id}/{platform}_profiles.{json,csv}` | JSON/CSV | Per simulation |
| Run state | `uploads/simulations/{sim_id}/run_state.json` | JSON | Per simulation run |
| Action logs | `uploads/simulations/{sim_id}/{platform}/actions.jsonl` | JSONL | Per simulation run |
| Simulation log | `uploads/simulations/{sim_id}/simulation.log` | Text | Per simulation run |
| IPC commands | `uploads/simulations/{sim_id}/ipc_commands/*.json` | JSON | Ephemeral |
| IPC responses | `uploads/simulations/{sim_id}/ipc_responses/*.json` | JSON | Ephemeral |
| Reports | `uploads/reports/{report_id}/report.json` | JSON | Until deleted |
| Report markdown | `uploads/reports/{report_id}/report.md` | Markdown | Per report |
| Report sections | `uploads/reports/{report_id}/section_*.md` | Markdown | Per report |
| Agent exec log | `uploads/reports/{report_id}/agent_log.jsonl` | JSONL | Per report |
| Console log | `uploads/reports/{report_id}/console_log.txt` | Text | Per report |
| Tasks | In-memory only (TaskManager singleton) | Python objects | Process lifetime |

### In-memory state

- `TaskManager` — Singleton with thread-safe dict. Tasks are lost on process restart.
- `SimulationManager._simulations` — In-memory cache of loaded simulation states (backed by disk).
- `SimulationRunner._run_states`, `_processes`, `_action_queues`, `_monitor_threads` — All in-memory. Running simulations are lost on restart (child processes are terminated via atexit cleanup).
- `ZepGraphMemoryManager._updaters` — In-memory registry of active graph memory updaters.

### External state

- **Zep Cloud** — The knowledge graph lives in Zep's cloud. Graph ID is stored in project/simulation JSON. If the Flask process dies, the Zep graph persists.

---

## 6. Frontend Architecture

### Stack
Vue 3 + Composition API, Vite build tool, Vue Router (HTML5 history mode), Axios HTTP client (5-min timeout), D3.js for graph visualization.

### Routes

| Path | Component | Purpose |
|------|-----------|---------|
| `/` | `Home.vue` | Landing page, project history |
| `/process/:projectId` | `MainView.vue` | Steps 1-2: Graph build + environment setup |
| `/simulation/:simulationId` | `SimulationView.vue` | Step 2 continued: review profiles/config |
| `/simulation/:simulationId/start` | `SimulationRunView.vue` | Step 3: Run simulation, live monitoring |
| `/report/:reportId` | `ReportView.vue` | Step 4: View generated report |
| `/interaction/:reportId` | `InteractionView.vue` | Step 5: Chat with agents/ReportAgent |

### Frontend ↔ Backend Communication

- All API calls go to `http://localhost:5001/api/*` (configurable via `VITE_API_BASE_URL`)
- Vite dev server proxies `/api` to the backend (configured in `vite.config.js`)
- Axios interceptors handle error responses and retry logic (`requestWithRetry` with exponential backoff, 3 attempts)
- Long-running operations (graph build, simulation prepare, report generate) return a `task_id`; frontend polls for progress
- Real-time simulation monitoring polls `GET /api/simulation/{id}/run-status/detail` periodically
- Report section streaming polls `GET /api/report/{id}/sections` and `GET /api/report/{id}/agent-log`

### Key Vue Components

- `Step1GraphBuild.vue` — File upload, ontology display, graph build progress, D3 graph visualization
- `Step2EnvSetup.vue` — Entity list, profile generation progress, config review
- `Step3Simulation.vue` — Simulation start controls, live action feed, round progress
- `Step4Report.vue` — Report generation progress, section-by-section display, markdown rendering
- `Step5Interaction.vue` — Chat interface with ReportAgent
- `GraphPanel.vue` — D3.js force-directed graph visualization
- `HistoryDatabase.vue` — Past simulation listing

---

## 7. LLM Usage Map

| Service | LLM Purpose | Client | Model |
|---------|-------------|--------|-------|
| `ontology_generator.py` | Generate entity/edge type schema from documents | `LLMClient` | Primary (qwen-plus default) |
| `oasis_profile_generator.py` | Generate detailed agent personas | `OpenAI` direct | Primary |
| `simulation_config_generator.py` | Generate time, event, agent, platform configs | `OpenAI` direct | Primary |
| `report_agent.py` | Plan report outline + generate sections (ReACT) | `LLMClient` | Primary or Boost |
| `zep_tools.py` (InsightForge) | Generate sub-questions for deep analysis | `LLMClient` | Primary |
| `zep_tools.py` (Interview) | Select agents + generate interview questions + summarize | `LLMClient` | Primary |
| `report_agent.py` (chat) | Conversational interaction with tool access | `LLMClient` | Primary or Boost |

`LLMClient` wraps OpenAI SDK with `<think>` tag stripping for reasoning models. Supports optional "boost" model configuration for performance-critical calls.

---

## 8. Async Patterns

MiroFish uses **Python threading** (not asyncio) for all background work:

1. **Graph build** — `threading.Thread(target=build_task, daemon=True)` in `graph.py`
2. **Simulation prepare** — `threading.Thread(target=prepare_task, daemon=True)` in `simulation.py`
3. **Simulation run** — `subprocess.Popen` + monitor `threading.Thread` in `simulation_runner.py`
4. **Report generation** — `threading.Thread(target=run_generate, daemon=True)` in `report.py`
5. **Zep memory updates** — Background `threading.Thread` in `zep_graph_memory_updater.py`
6. **Profile generation** — `concurrent.futures.ThreadPoolExecutor` for parallel LLM calls in `oasis_profile_generator.py`
7. **Zep searches** — `concurrent.futures.ThreadPoolExecutor` for parallel edge/node searches

All threads are daemon threads (die with the main process). The `SimulationRunner.register_cleanup()` registers an `atexit` handler and `SIGTERM`/`SIGINT` signal handlers to kill child processes.

---

## 9. Configuration

All via environment variables (`.env` file at project root):

| Variable | Purpose | Default |
|----------|---------|---------|
| `LLM_API_KEY` | Primary LLM API key | Required |
| `LLM_BASE_URL` | Primary LLM endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_MODEL_NAME` | Primary model | `qwen-plus` |
| `ZEP_API_KEY` | Zep Cloud API key | Required |
| `LLM_BOOST_API_KEY` | Optional faster LLM | — |
| `LLM_BOOST_BASE_URL` | Optional faster endpoint | — |
| `LLM_BOOST_MODEL_NAME` | Optional faster model | — |

**Flask config:** Debug mode on by default, CORS enabled for all `/api/*`, max upload 50MB, allowed extensions: pdf/md/txt/markdown.

---

## 10. Error Handling and Resilience

- **Zep API calls** — Exponential backoff retry (3 attempts, 2s initial delay) in `zep_entity_reader.py`, `zep_paging.py`, `oasis_profile_generator.py`
- **LLM calls** — No built-in retry in `LLMClient`; frontend has `requestWithRetry` (3 attempts)
- **Simulation subprocess** — Monitored by thread; exit code checked; stderr captured to log file
- **Graph memory updates** — 3 retries with increasing delay; failed batches are counted but don't crash the simulation
- **`<think>` tag handling** — `LLMClient.chat()` strips `<think>...</think>` blocks from reasoning model outputs
- **Markdown code fence stripping** — `LLMClient.chat_json()` strips ```json``` fences before JSON parsing

---

## 11. Corrections to External Analysis

The project prompt assumed FastAPI; the backend is actually **Flask**. The prompt mentioned `httpx`; the codebase uses the **synchronous `zep_cloud` SDK** and **OpenAI SDK** (both blocking). There is no `memory_factory.py` or abstract memory provider pattern — Zep is directly instantiated throughout.

The backend is **not** async (no `async/await`, no `httpx`). It uses Flask with Python threading for concurrency.
