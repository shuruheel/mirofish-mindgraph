<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MiroFish Logo" width="75%"/>

<a href="https://trendshift.io/repositories/16144" target="_blank"><img src="https://trendshift.io/api/badge/repositories/16144" alt="666ghj%2FMiroFish | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

*A Swarm Intelligence Engine for Predictive Simulation*

<a href="https://www.shanda.com/" target="_blank"><img src="./static/image/shanda_logo.png" alt="666ghj%2MiroFish | Shanda" height="40"/></a>

[![GitHub Stars](https://img.shields.io/github/stars/666ghj/MiroFish?style=flat-square&color=DAA520)](https://github.com/666ghj/MiroFish/stargazers)
[![GitHub Watchers](https://img.shields.io/github/watchers/666ghj/MiroFish?style=flat-square)](https://github.com/666ghj/MiroFish/watchers)
[![GitHub Forks](https://img.shields.io/github/forks/666ghj/MiroFish?style=flat-square)](https://github.com/666ghj/MiroFish/network)
[![Docker](https://img.shields.io/badge/Docker-Build-2496ED?style=flat-square&logo=docker&logoColor=white)](https://hub.docker.com/)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/666ghj/MiroFish)

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=flat-square&logo=discord&logoColor=white)](http://discord.gg/ePf5aPaHnA)
[![X](https://img.shields.io/badge/X-Follow-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/mirofish_ai)
[![Instagram](https://img.shields.io/badge/Instagram-Follow-E4405F?style=flat-square&logo=instagram&logoColor=white)](https://www.instagram.com/mirofish_ai/)

</div>

## Overview

**MiroFish** is an AI prediction engine powered by multi-agent swarm intelligence. Upload seed materials — breaking news, policy drafts, financial signals, even novel manuscripts — and MiroFish automatically constructs a high-fidelity parallel digital world. Thousands of autonomous agents with distinct personalities, persistent memory, and behavioral logic interact freely, producing emergent social dynamics. Inject variables from a god's-eye view to explore how the future unfolds.

> **Input:** Upload documents (PDF/MD/TXT) and describe your prediction scenario in natural language.
> **Output:** A detailed prediction report and a fully interactive digital world you can query.

## MindGraph Integration

This fork replaces the original Zep Cloud memory layer with **[MindGraph Cloud](https://mindgraph.cloud)**, a structured knowledge graph with cognitive retrieval. The difference is fundamental — Zep stores flat memory entries retrieved by vector similarity, while MindGraph builds a richly connected epistemic graph that agents reason over.

**What MindGraph provides that Zep cannot:**

| Capability | MindGraph | Zep |
|---|---|---|
| **Epistemic graph structure** | 6-layer cognitive architecture (entities, relationships, observations, epistemic states, temporal facts, summaries) | Flat fact/memory list |
| **Ontology-driven extraction** | LLM generates a domain-specific ontology per project; graph construction follows it | Generic entity extraction |
| **GraphRAG retrieval** | Multi-hop reasoning across entity relationships and temporal facts | Single-hop vector similarity |
| **Salience decay** | Facts fade over time like real memory — recent observations outweigh old ones | All memories equally weighted |
| **Live simulation writeback** | Agent actions (claims, decisions, anomalies) are written to the graph in real-time during simulation | Post-hoc memory storage |
| **Structured cognitive tools** | InsightForge (multi-hop analysis), PanoramaSearch (graph overview), Interview (agent querying), QuickSearch — all operating over the post-simulation graph | Basic search |
| **Project isolation** | Namespace isolation via `agent_id` — one API key, multiple independent project graphs | Separate collections |

In practice, this means the ReportAgent can answer questions like *"What chain of events led Agent X to change their stance?"* by traversing relationship chains in the graph — something impossible with flat memory retrieval.

## Workflow

MiroFish supports two launch modes:

**Mode A — Upload Documents**: Upload seed materials (PDF/MD/TXT). MindGraph automatically builds a knowledge graph, then simulation begins.

**Mode B — Connect to MindGraph**: Skip document upload and connect directly to an existing [MindGraph Cloud](https://mindgraph.cloud) knowledge graph. Ideal for users who have already built their graph.

1. **Graph Construction** — LLM generates a domain-specific ontology, then chunks and extracts entities/relationships into MindGraph's epistemic graph
2. **Environment Setup** — Entities are read from the graph; GraphRAG-enhanced context drives LLM persona generation; simulation parameters are auto-configured
3. **Simulation** — Dual-platform parallel simulation (Twitter + Reddit via OASIS); agent activity is written back to MindGraph in real-time with salience decay modeling memory fading
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

## Live Demo

Try a pre-built prediction simulation: [mirofish-live-demo](https://666ghj.github.io/mirofish-demo/)

## Screenshots

<div align="center">
<table>
<tr>
<td><img src="./static/image/Screenshot/运行截图1.png" alt="Screenshot 1" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图2.png" alt="Screenshot 2" width="100%"/></td>
</tr>
<tr>
<td><img src="./static/image/Screenshot/运行截图3.png" alt="Screenshot 3" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图4.png" alt="Screenshot 4" width="100%"/></td>
</tr>
<tr>
<td><img src="./static/image/Screenshot/运行截图5.png" alt="Screenshot 5" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图6.png" alt="Screenshot 6" width="100%"/></td>
</tr>
</table>
</div>

## Demo Videos

### Wuhan University Public Opinion Simulation

<div align="center">
<a href="https://www.bilibili.com/video/BV1VYBsBHEMY/" target="_blank"><img src="./static/image/武大模拟演示封面.png" alt="MiroFish Demo Video" width="75%"/></a>

Full demo of a public opinion prediction generated with BettaFish
</div>

### Dream of the Red Chamber — Lost Ending Prediction

<div align="center">
<a href="https://www.bilibili.com/video/BV1cPk3BBExq" target="_blank"><img src="./static/image/红楼梦模拟推演封面.jpg" alt="MiroFish Demo Video" width="75%"/></a>

MiroFish predicts the lost ending based on the first 80 chapters
</div>

## Acknowledgments

**MiroFish has received strategic support and incubation from Shanda Group.**

- Simulation engine powered by **[OASIS](https://github.com/camel-ai/oasis)** (CAMEL-AI)
- Knowledge graph and cognitive memory powered by **[MindGraph Cloud](https://mindgraph.cloud)** — replacing the original Zep Cloud integration with a structured epistemic graph, GraphRAG retrieval, salience decay, and real-time simulation writeback

## Contact

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=flat-square&logo=discord&logoColor=white)](http://discord.gg/ePf5aPaHnA) [![X](https://img.shields.io/badge/X-Follow-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/mirofish_ai)

The MiroFish team is hiring (full-time and internships). If you're interested in multi-agent simulation and LLM applications, reach out at **mirofish@shanda.com**.

## Star History

<a href="https://www.star-history.com/#666ghj/MiroFish&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=666ghj/MiroFish&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=666ghj/MiroFish&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=666ghj/MiroFish&type=date&legend=top-left" />
 </picture>
</a>

## License

[AGPL-3.0](LICENSE)
