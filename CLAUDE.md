# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiroFish is a multi-agent swarm intelligence simulation engine for predictive analytics. Users upload documents, a knowledge graph is built via Zep Cloud, thousands of autonomous AI agents are generated with unique personas, and OASIS (CAMEL-AI) runs parallel Twitter/Reddit simulations to predict outcomes. The system follows a 5-step pipeline: Graph Construction → Environment Setup → Simulation → Report Generation → Deep Interaction.

## Commands

```bash
# Setup
cp .env.example .env              # Then fill in LLM_API_KEY, ZEP_API_KEY, etc.
npm run setup:all                  # Install frontend (npm) + backend (uv) deps

# Development
npm run dev                        # Runs frontend (port 3000) + backend (port 5001) concurrently
npm run frontend                   # Frontend only (Vite dev server)
npm run backend                    # Backend only (Flask)

# Build
npm run build                      # Build frontend for production

# Docker
docker compose up -d               # Run full stack in containers
```

There are no test or lint commands configured in this project.

## Architecture

**Tech stack**: Vue 3 + Vite frontend, Flask backend, Zep Cloud (knowledge graph/memory), OASIS/CAMEL-AI (multi-agent simulation), OpenAI SDK (LLM calls, any OpenAI-compatible API).

### Backend (`backend/`)

Flask app factory in `app/__init__.py`. Three API blueprints:

- **`api/graph.py`** — Project creation, file upload/parsing, ontology generation, Zep knowledge graph construction
- **`api/simulation.py`** — Entity reading from graph, agent profile generation, OASIS dual-platform simulation management (Twitter + Reddit run in parallel child processes)
- **`api/report.py`** — Report generation via tool-augmented LLM agent, report retrieval, conversational interaction

Key services in `app/services/`:

- **`simulation_runner.py`** — Spawns OASIS simulations as child processes via `simulation_ipc.py`
- **`simulation_manager.py`** — State machine: CREATED → PREPARING → READY → RUNNING → COMPLETED
- **`graph_builder.py`** / **`ontology_generator.py`** — Text chunking → Zep graph construction with LLM-generated ontologies
- **`oasis_profile_generator.py`** / **`simulation_config_generator.py`** — LLM-driven agent persona and simulation parameter generation
- **`report_agent.py`** — ReportAgent with tool-use capabilities (queries simulation state, entity context)
- **`llm_client.py`** — Unified LLM client wrapping OpenAI SDK; supports optional "boost" model config

Graph building and report generation are async tasks tracked via `models/task.py` with progress polling.

### Frontend (`frontend/`)

Vue 3 with Composition API. Routes:

- `/` → Home
- `/process/:projectId` → Steps 1-2 (graph build + environment setup)
- `/simulation/:simulationId` → Steps 3 (run simulation)
- `/report/:reportId` → Step 4 (view report)
- `/interaction/:reportId` → Step 5 (chat with agents)

API clients in `src/api/` use Axios with 5-minute timeout. D3.js powers knowledge graph visualization in `GraphPanel.vue`. The frontend proxies `/api` requests to the backend via Vite config.

### Configuration

All config via environment variables (see `.env.example`):

- `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` — Primary LLM (default model: qwen-plus via Aliyun DashScope)
- `ZEP_API_KEY` — Zep Cloud for knowledge graph storage
- `LLM_BOOST_*` — Optional secondary LLM for performance-critical calls
- File uploads: max 50MB, accepts `.pdf`, `.md`, `.txt`, `.markdown`

### Key Patterns

- Backend uses server-side state for projects and simulations (no client-side persistence)
- LLM calls use OpenAI SDK format throughout — any OpenAI-compatible API works
- The `<think>` tag stripping in `report_agent.py` and `simulation.py` handles reasoning model output (e.g., MiniMax, GLM)
- Bilingual support (Chinese/English) — UI text and LLM prompts use Chinese

## License

AGPL-3.0
