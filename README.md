<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MiroFish Logo" width="75%"/>

*A Swarm Intelligence Engine for Predictive Simulation*

<a href="https://www.shanda.com/" target="_blank"><img src="./static/image/shanda_logo.png" alt="Shanda Group" height="40"/></a>

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=flat-square&logo=discord&logoColor=white)](http://discord.gg/ePf5aPaHnA)
[![X](https://img.shields.io/badge/X-Follow-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/mirofish_ai)

</div>

## Overview

**MiroFish** is an AI prediction engine powered by multi-agent swarm intelligence. Upload seed materials — breaking news, policy drafts, financial signals, even novel manuscripts — and MiroFish automatically constructs a high-fidelity parallel digital world. Thousands of autonomous agents with distinct personalities, persistent memory, and behavioral logic interact freely, producing emergent social dynamics. Inject variables from a god's-eye view to explore how the future unfolds.

> **Input:** Upload documents (PDF/MD/TXT) and describe your prediction scenario in natural language.
> **Output:** A detailed prediction report and a fully interactive digital world you can query.

## MindGraph Integration

This fork replaces the original Zep Cloud memory layer with **[MindGraph Cloud](https://mindgraph.cloud)**. Both provide knowledge graphs, but MindGraph's multi-layer cognitive architecture changes what the system can represent and reason over.

**Key differences from the Zep-based original:**

| Capability | MindGraph | Zep (original) |
|---|---|---|
| **Graph structure** | 6-layer cognitive architecture — entities, relationships, and typed epistemic nodes (Claims, Evidence, Hypotheses, Decisions, Observations) across Reality, Epistemic, Intent, Action, Agent, and Memory layers | 3-tier structure — episodes, entity/relationship subgraph, and community clusters |
| **Simulation writeback** | Typed node creation — agent posts become Journal nodes, decisions become Decision/Option nodes, anomalies are flagged, all linked to Agent nodes with explicit edges | Uniform text ingestion — all activity is converted to plain text and fed through the same episode-to-graph pipeline |
| **Agent context during simulation** | `GraphContextProvider` injects per-round graph context into agents: entity relationships, claims, and related agents' recent activity, retrieved via epistemic layer queries | Graph data is used post-simulation only, by the Report Agent for retrieval |
| **Entity selection** | Relevance-based ranking using epistemic retrieval — selects the most relevant entities for the simulation scenario | Returns all entities matching the ontology |
| **Retrieval** | Hybrid search (BM25 + semantic + RRF) plus `retrieve_context()` for graph-augmented RAG with layer filtering | Hybrid search with cross-encoder reranking, community-aware retrieval |

In practice, this means agents are graph-aware *during* simulation — their actions create structured nodes that the ReportAgent can later traverse to answer questions like *"What chain of events led Agent X to change their stance?"*

## Workflow

MiroFish supports two launch modes:

**Mode A — Upload Documents**: Upload seed materials (PDF/MD/TXT). MindGraph automatically builds a knowledge graph, then simulation begins.

**Mode B — Connect to MindGraph**: Skip document upload and connect directly to an existing [MindGraph Cloud](https://mindgraph.cloud) knowledge graph. Ideal for users who have already built their graph.

1. **Graph Construction** — LLM generates a domain-specific ontology, then chunks and extracts entities/relationships into MindGraph's epistemic graph
2. **Environment Setup** — Entities are read from the graph; GraphRAG-enhanced context drives LLM persona generation; simulation parameters are auto-configured
3. **Simulation** — Dual-platform parallel simulation (Twitter + Reddit via OASIS); agent activity is written back to MindGraph as typed nodes in real-time
4. **Report Generation** — ReportAgent queries the post-simulation graph through MindGraph's cognitive retrieval tools (InsightForge, PanoramaSearch, Interview, QuickSearch)
5. **Deep Interaction** — Chat with any individual agent in the simulated world, or continue querying the ReportAgent

## Quick Start

### Option 1: Source Code (Recommended)

#### Prerequisites

| Tool | Version | Description | Check |
|------|---------|-------------|-------|
| **Node.js** | 18+ | Frontend runtime (includes npm) | `node -v` |
| **Python** | 3.11 - 3.12 | Backend runtime | `python --version` |
| **uv** | Latest | Python package manager | `uv --version` |

#### 1. Configure Environment Variables

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

**Required:**

```env
# LLM API (any OpenAI SDK-compatible endpoint)
# Recommended: Google Gemini 2.5 Flash via OpenRouter
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_MODEL_NAME=google/gemini-2.5-flash

# MindGraph Cloud (knowledge graph storage & cognitive retrieval)
# Get an API key: https://mindgraph.cloud/dashboard/keys
MINDGRAPH_API_KEY=your_mindgraph_api_key
MINDGRAPH_BASE_URL=https://api.mindgraph.cloud
```

#### 2. Install Dependencies

```bash
# Install all dependencies (frontend + backend)
npm run setup:all
```

Or step by step:

```bash
npm run setup          # Node dependencies (root + frontend)
npm run setup:backend  # Python dependencies (auto-creates virtualenv)
```

#### 3. Start

```bash
npm run dev  # Starts frontend (port 3000) + backend (port 5001)
```

Individual services:

```bash
npm run backend   # Backend only
npm run frontend  # Frontend only
```

### Option 2: Docker

```bash
cp .env.example .env   # Configure API keys
docker compose up -d   # Pull and start
```

Reads `.env` from the project root. Maps ports `3000` (frontend) and `5001` (backend).

## Acknowledgments

**MiroFish has received strategic support and incubation from Shanda Group.**

- Simulation engine powered by **[OASIS](https://github.com/camel-ai/oasis)** (CAMEL-AI)
- Knowledge graph and cognitive memory powered by **[MindGraph Cloud](https://mindgraph.cloud)** — replacing the original Zep Cloud integration with a multi-layer cognitive graph, typed simulation writeback, and real-time agent context injection

## Contact

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=flat-square&logo=discord&logoColor=white)](http://discord.gg/ePf5aPaHnA) [![X](https://img.shields.io/badge/X-Follow-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/mirofish_ai)

The MiroFish team is hiring (full-time and internships). If you're interested in multi-agent simulation and LLM applications, reach out at **mirofish@shanda.com**.

## License

[AGPL-3.0](LICENSE)
