"""
Comprehensive MindGraph Integration Test (standalone)

Tests all MindGraph API endpoints used by MiroFish against the live API.
Uses requests directly to avoid Flask app import issues.

Run: python3 backend/tests/test_mindgraph_integration.py
"""

import json
import time
import requests

API_KEY = "mg_live_7wrlvd84doov4ixiy6hlev2n63ch7h0b"
BASE_URL = "https://api.mindgraph.cloud"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name, detail=""):
        self.passed += 1
        print(f"  \033[32mPASS\033[0m {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name, error):
        self.failed += 1
        self.errors.append((name, str(error)))
        print(f"  \033[31mFAIL\033[0m {name} — {error}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        if self.failed == 0:
            print(f"\033[32mAll {total} tests passed.\033[0m")
        else:
            print(f"\033[31m{self.failed}/{total} tests failed:\033[0m")
            for name, err in self.errors:
                print(f"  - {name}: {err}")
        print(f"{'='*60}")
        return self.failed == 0


def api(method, path, json_body=None, params=None, expect_status=None):
    """Make an API call and return the result."""
    url = f"{BASE_URL}{path}"
    resp = requests.request(method, url, headers=HEADERS, json=json_body, params=params, timeout=60)
    if expect_status and resp.status_code != expect_status:
        raise Exception(f"HTTP {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    if resp.status_code == 204:
        return {}
    return resp.json()


def run_tests():
    r = TestResult()
    PROJECT_ID = f"mirofish_test_{int(time.time())}"
    print(f"Test project namespace: {PROJECT_ID}")
    print(f"{'='*60}")

    created_uids = {}

    # ── 1. Connectivity ──
    print("\n[1] API Connectivity")
    try:
        # Simple health check - list nodes (should return empty for new namespace)
        result = api("GET", "/nodes", params={"agent": PROJECT_ID, "limit": 1})
        r.ok("API connectivity", f"response type={type(result).__name__}")
    except Exception as e:
        r.fail("API connectivity", e)
        r.summary()
        return False

    # ── 2. Ingestion ──
    print("\n[2] Ingestion Endpoints")

    # 2a. ingest_chunk (sync)
    try:
        result = api("POST", "/ingest/chunk", json_body={
            "content": "北京市最近出台了新的租房政策，要求房东必须在住房租赁平台上登记所有出租房源。这一政策旨在规范租赁市场，保护租户权益。",
            "agent_id": PROJECT_ID,
            "layers": ["reality", "epistemic"],
            "label": "[TestAgent] R1",
            "chunk_type": "agent_post",
        })
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        chunk_uid = result.get("chunk_uid", "")
        extracted = result.get("extracted_node_uids", [])
        nodes_created = result.get("nodes_created", 0)
        r.ok("ingest_chunk", f"chunk_uid={chunk_uid[:16] if chunk_uid else 'NONE'}, "
             f"extracted={len(extracted)}, nodes_created={nodes_created}")
        created_uids["chunk"] = chunk_uid
        created_uids["extracted"] = extracted
        print(f"    Response keys: {list(result.keys())}")
    except Exception as e:
        r.fail("ingest_chunk", e)

    # 2b. ingest_agent_post (simulated — same as above with agent name prefix)
    try:
        result = api("POST", "/ingest/chunk", json_body={
            "content": "张明: 我认为这个新的租房政策对租户来说是好事，可以减少黑中介的问题。",
            "agent_id": PROJECT_ID,
            "layers": ["reality", "epistemic"],
            "label": "[张明] twitter R1",
            "chunk_type": "agent_post",
        })
        assert isinstance(result, dict)
        r.ok("ingest_agent_post pattern", f"keys={list(result.keys())[:6]}")
        # Collect extracted UIDs
        for uid in result.get("extracted_node_uids", []):
            created_uids.setdefault("all_extracted", []).append(uid)
    except Exception as e:
        r.fail("ingest_agent_post pattern", e)

    # 2c. ingest_document (async)
    try:
        result = api("POST", "/ingest/document", json_body={
            "content": "这是一份关于租房市场调研的长文档。" * 50,
            "agent_id": PROJECT_ID,
            "source_name": "test_document.txt",
            "layers": ["reality", "epistemic"],
        })
        job_id = result.get("job_id", "")
        r.ok("ingest_document (async)", f"job_id={str(job_id)[:20]}, keys={list(result.keys())}")
        created_uids["job_id"] = str(job_id)
    except Exception as e:
        r.fail("ingest_document", e)

    time.sleep(3)  # let async processing start

    # ── 3. Search & Retrieval ──
    print("\n[3] Search & Retrieval")

    for action_name, action in [("hybrid", "hybrid"), ("text", "text"), ("semantic", "semantic")]:
        try:
            result = api("POST", "/retrieve", json_body={
                "action": action,
                "query": "租房政策",
                "limit": 5,
                "agent_id": PROJECT_ID,
            })
            results_list = result.get("results", [])
            r.ok(f"search_{action_name}", f"results={len(results_list)}")
        except Exception as e:
            r.fail(f"search_{action_name}", e)

    # retrieve_context (RAG)
    try:
        result = api("POST", "/retrieve/context", json_body={
            "query": "租房政策影响",
            "k": 3,
            "depth": 1,
            "agent_id": PROJECT_ID,
        })
        chunks = result.get("chunks", [])
        graph = result.get("graph", {})
        r.ok("retrieve_context (RAG)", f"chunks={len(chunks)}, graph_keys={list(graph.keys()) if isinstance(graph, dict) else 'N/A'}")
    except Exception as e:
        r.fail("retrieve_context", e)

    # Cognitive queries
    for qname, action in [("weak_claims", "weak_claims"), ("contradictions", "unresolved_contradictions"), ("open_questions", "open_questions")]:
        try:
            result = api("POST", "/retrieve", json_body={
                "action": action,
                "limit": 5,
                "agent_id": PROJECT_ID,
            })
            r.ok(f"cognitive: {qname}", f"results={len(result.get('results', []))}")
        except Exception as e:
            r.fail(f"cognitive: {qname}", e)

    # ── 4. Node & Edge Listing ──
    print("\n[4] Node & Edge Listing")

    try:
        result = api("GET", "/nodes", params={"agent": PROJECT_ID, "limit": 20})
        items = result.get("items", result) if isinstance(result, dict) else result
        if not isinstance(items, list):
            items = []
        r.ok("list_nodes", f"count={len(items)}")
        if items:
            created_uids["first_node"] = items[0].get("uid", "")
            # Show node types
            types = set(n.get("node_type", "?") for n in items)
            print(f"    Node types found: {types}")
    except Exception as e:
        r.fail("list_nodes", e)

    try:
        result = api("GET", "/edges", params={"agent": PROJECT_ID, "limit": 20})
        items = result.get("items", result) if isinstance(result, dict) else result
        if not isinstance(items, list):
            items = []
        r.ok("list_edges", f"count={len(items)}")
    except Exception as e:
        r.fail("list_edges", e)

    # ── 5. Single Node Operations ──
    print("\n[5] Single Node Operations")

    node_uid = created_uids.get("first_node", "")
    if node_uid:
        try:
            node = api("GET", f"/node/{node_uid}")
            r.ok("get_node", f"label={node.get('label', 'N/A')[:30]}, type={node.get('node_type', 'N/A')}")
        except Exception as e:
            r.fail("get_node", e)

        try:
            result = api("GET", f"/neighborhood/{node_uid}", params={"depth": 1})
            n_nodes = len(result.get("nodes", []))
            n_edges = len(result.get("edges", []))
            r.ok("get_neighborhood", f"nodes={n_nodes}, edges={n_edges}")
        except Exception as e:
            r.fail("get_neighborhood", e)

        try:
            result = api("GET", f"/node/{node_uid}/history")
            history = result if isinstance(result, list) else result.get("items", [])
            r.ok("get_node_history", f"versions={len(history)}")
        except Exception as e:
            r.fail("get_node_history", e)

        try:
            result = api("GET", f"/chain/{node_uid}", params={"max_depth": 3})
            r.ok("traverse_chain", f"keys={list(result.keys())[:5]}")
        except Exception as e:
            r.fail("traverse_chain", e)
    else:
        for name in ["get_node", "get_neighborhood", "get_node_history", "traverse_chain"]:
            r.fail(name, "No node UID available from listing")

    # ── 6. Reality Layer — Entity CRUD ──
    print("\n[6] Reality Layer — Entity CRUD")

    entity_uid = ""
    try:
        result = api("POST", "/reality/entity", json_body={
            "action": "create",
            "label": "北京市住建局",
            "props": {"entity_type": "Organization", "description": "负责北京市住房建设的政府部门", "role": "regulator"},
            "agent_id": PROJECT_ID,
        })
        entity_uid = result.get("uid", "")
        r.ok("create_entity", f"uid={entity_uid[:16] if entity_uid else 'NONE'}")
        created_uids["entity"] = entity_uid
    except Exception as e:
        r.fail("create_entity", e)

    if entity_uid:
        try:
            result = api("POST", "/reality/entity", json_body={
                "action": "resolve",
                "label": "北京市住建局",
                "agent_id": PROJECT_ID,
            })
            r.ok("resolve_entity", f"uid={result.get('uid', 'N/A')[:16]}")
        except Exception as e:
            r.fail("resolve_entity", e)

        try:
            result = api("POST", "/reality/entity", json_body={
                "action": "fuzzy_resolve",
                "label": "住建局",
                "limit": 3,
                "agent_id": PROJECT_ID,
            })
            r.ok("fuzzy_resolve_entity", f"keys={list(result.keys())[:5]}")
        except Exception as e:
            r.fail("fuzzy_resolve_entity", e)

    # relate_entities (with agent_id)
    if entity_uid and node_uid:
        try:
            result = api("POST", "/reality/entity", json_body={
                "action": "relate",
                "source_uid": entity_uid,
                "target_uid": node_uid,
                "edge_type": "Related",
                "props": {},
                "agent_id": PROJECT_ID,
            })
            r.ok("relate_entities (with agent_id)", f"type={type(result).__name__}")
        except Exception as e:
            r.fail("relate_entities", e)

    # ── 7. Epistemic Layer ──
    print("\n[7] Epistemic Layer")

    hypothesis_uid = ""
    try:
        result = api("POST", "/epistemic/inquiry", json_body={
            "action": "hypothesis",
            "label": "新租房政策将在6个月内使黑中介减少30%",
            "confidence": 0.5,
            "props": {"statement": "新租房政策将在6个月内使黑中介减少30%", "hypothesis_type": "predictive", "status": "proposed"},
            "agent_id": PROJECT_ID,
        })
        hypothesis_uid = result.get("uid", "")
        r.ok("add_hypothesis", f"uid={hypothesis_uid[:16] if hypothesis_uid else 'NONE'}")
        created_uids["hypothesis"] = hypothesis_uid
    except Exception as e:
        r.fail("add_hypothesis", e)

    try:
        result = api("POST", "/epistemic/argument", json_body={
            "claim": {
                "label": "租赁平台登记制度已在深圳成功实施",
                "confidence": 0.7,
                "props": {"content": "租赁平台登记制度已在深圳成功实施", "claim_type": "evidence_based", "proposed_by": "李教授"},
            },
            "evidence": [{"label": "深圳2023数据", "props": {"description": "黑中介投诉下降40%", "evidence_type": "referenced_content"}}],
            "agent_id": PROJECT_ID,
        })
        r.ok("add_claim (with evidence)", f"keys={list(result.keys())[:5]}")
    except Exception as e:
        r.fail("add_claim", e)

    anomaly_uid = ""
    try:
        result = api("POST", "/epistemic/inquiry", json_body={
            "action": "anomaly",
            "label": "[异常] 张明: 行为不一致",
            "props": {"description": "张明 (opposing) 发表支持性内容", "anomaly_type": "behavioral_inconsistency", "severity": "medium"},
            "agent_id": PROJECT_ID,
        })
        anomaly_uid = result.get("uid", "")
        r.ok("record_anomaly", f"uid={anomaly_uid[:16] if anomaly_uid else 'NONE'}")
        created_uids["anomaly"] = anomaly_uid
    except Exception as e:
        r.fail("record_anomaly", e)

    try:
        result = api("POST", "/epistemic/structure", json_body={
            "action": "pattern",
            "label": "回声室效应",
            "props": {"name": "回声室效应", "description": "支持派互相转发率85%", "pattern_type": "emergent", "instance_count": 42},
            "agent_id": PROJECT_ID,
        })
        r.ok("record_pattern", f"keys={list(result.keys())[:5]}")
    except Exception as e:
        r.fail("record_pattern", e)

    # ── 8. Intent Layer ──
    print("\n[8] Intent Layer")

    goal_uid = ""
    try:
        result = api("POST", "/intent/commitment", json_body={
            "action": "goal",
            "label": "张明: opposing stance",
            "props": {"description": "Agent张明 has opposing stance", "priority": "high", "goal_type": "social", "status": "active"},
            "agent_id": PROJECT_ID,
        })
        goal_uid = result.get("uid", "")
        r.ok("create_goal", f"uid={goal_uid[:16] if goal_uid else 'NONE'}")
        created_uids["goal"] = goal_uid
    except Exception as e:
        r.fail("create_goal", e)

    decision_uid = ""
    try:
        # Step 1: open_decision
        result = api("POST", "/intent/deliberation", json_body={
            "action": "open_decision",
            "label": "张明 decided to comment",
            "props": {"description": "张明 decided to publicly comment on housing policy"},
            "agent_id": PROJECT_ID,
        })
        decision_uid = result.get("uid", "")
        r.ok("record_decision step1 (open)", f"uid={decision_uid[:16] if decision_uid else 'NONE'}")

        if decision_uid:
            # Step 2: add_option
            api("POST", "/intent/deliberation", json_body={
                "action": "add_option",
                "decision_uid": decision_uid,
                "label": "发表反对帖子",
                "props": {"description": "发表一篇反对新政策的帖子"},
                "agent_id": PROJECT_ID,
            })
            r.ok("record_decision step2 (add_option)", "")

            # Step 3: resolve
            api("POST", "/intent/deliberation", json_body={
                "action": "resolve",
                "decision_uid": decision_uid,
                "props": {"decision_rationale": "Agent stance: opposing, sentiment: -0.5"},
                "agent_id": PROJECT_ID,
            })
            r.ok("record_decision step3 (resolve)", "")
        created_uids["decision"] = decision_uid
    except Exception as e:
        r.fail("record_decision", e)

    # ── 9. Memory Layer ──
    print("\n[9] Memory Layer")

    session_uid = ""
    try:
        result = api("POST", "/memory/session", json_body={
            "action": "open",
            "label": "Test Simulation Session",
            "props": {"focus_summary": "Test Simulation Session"},
            "agent_id": PROJECT_ID,
        })
        session_uid = result.get("uid", "")
        r.ok("open_session", f"uid={session_uid[:16] if session_uid else 'NONE'}")
    except Exception as e:
        r.fail("open_session", e)

    if session_uid:
        try:
            result = api("POST", "/memory/session", json_body={
                "action": "trace",
                "session_uid": session_uid,
                "label": "社交活动记录",
                "props": {"content": "张明: 点赞了李教授的帖子\n王芳: 转发了张明的帖子", "trace_type": "simulation_activity"},
                "agent_id": PROJECT_ID,
            })
            r.ok("trace_session", f"keys={list(result.keys())[:5]}")
        except Exception as e:
            r.fail("trace_session", e)

        try:
            result = api("POST", "/memory/session", json_body={
                "action": "close",
                "session_uid": session_uid,
                "agent_id": PROJECT_ID,
            })
            r.ok("close_session", f"keys={list(result.keys())[:5]}")
        except Exception as e:
            r.fail("close_session", e)

    # distill
    source_uids = [uid for uid in [created_uids.get("chunk", ""), hypothesis_uid] if uid]
    if source_uids:
        try:
            result = api("POST", "/memory/distill", json_body={
                "label": "Test Simulation Summary",
                "summarizes_uids": source_uids,
                "props": {"content": "基于测试节点的蒸馏摘要"},
                "agent_id": PROJECT_ID,
            })
            r.ok("distill", f"keys={list(result.keys())[:5]}")
        except Exception as e:
            r.fail("distill", e)
    else:
        r.fail("distill", "No source UIDs")

    # ── 10. Agent Layer — register_agent_node ──
    print("\n[10] Agent Layer")

    agent_uid = ""
    # Try POST /action/execution with register_agent
    try:
        result = api("POST", "/action/execution", json_body={
            "action": "register_agent",
            "label": "张明",
            "summary": "Person: opposing stance, sentiment=-0.5",
            "props": {"entity_type": "Person", "stance": "opposing", "sentiment_bias": -0.5, "influence_weight": 0.8},
            "agent_id": PROJECT_ID,
        })
        agent_uid = result.get("uid", "")
        r.ok("register_agent_node (/action/execution)", f"uid={agent_uid[:16] if agent_uid else 'NONE'}")
        created_uids["agent"] = agent_uid
    except Exception as e:
        r.fail("register_agent_node (/action/execution)", f"{e}")
        # Fallback 1: POST /node
        print("    Trying fallback: POST /node with node_type=Agent ...")
        try:
            result = api("POST", "/node", json_body={
                "label": "张明",
                "node_type": "Agent",
                "props": {"entity_type": "Person", "stance": "opposing", "sentiment_bias": -0.5, "influence_weight": 0.8},
                "agent_id": PROJECT_ID,
            })
            agent_uid = result.get("uid", "")
            r.ok("add_node (Agent type)", f"uid={agent_uid[:16] if agent_uid else 'NONE'}")
            created_uids["agent"] = agent_uid
        except Exception as e2:
            r.fail("add_node (Agent fallback)", f"{e2}")
            # Fallback 2: create_entity
            print("    Trying fallback: POST /reality/entity ...")
            try:
                result = api("POST", "/reality/entity", json_body={
                    "action": "create",
                    "label": "张明 (Agent)",
                    "props": {"entity_type": "Agent", "stance": "opposing", "sentiment_bias": -0.5, "influence_weight": 0.8},
                    "agent_id": PROJECT_ID,
                })
                agent_uid = result.get("uid", "")
                r.ok("create_entity (Agent fallback)", f"uid={agent_uid[:16] if agent_uid else 'NONE'}")
                created_uids["agent"] = agent_uid
            except Exception as e3:
                r.fail("create_entity (Agent fallback)", f"{e3}")

    # ── 11. Edge Creation ──
    print("\n[11] Edge Creation")

    link_target = (created_uids.get("extracted", [None]) or [None])[0] or goal_uid or node_uid
    if agent_uid and link_target:
        # Try POST /link
        try:
            result = api("POST", "/link", json_body={
                "from_uid": agent_uid,
                "to_uid": link_target,
                "edge_type": "AUTHORED",
                "agent_id": PROJECT_ID,
            })
            r.ok("add_link (/link)", f"result_type={type(result).__name__}, keys={list(result.keys())[:5] if isinstance(result, dict) else 'N/A'}")
        except Exception as e:
            r.fail("add_link (/link)", f"{e}")
            # Fallback: POST /edge
            print("    Trying fallback: POST /edge ...")
            try:
                result = api("POST", "/edge", json_body={
                    "from_uid": agent_uid,
                    "to_uid": link_target,
                    "edge_type": "AUTHORED",
                    "agent_id": PROJECT_ID,
                })
                r.ok("add_edge (/edge fallback)", f"type={type(result).__name__}")
            except Exception as e2:
                r.fail("add_edge (/edge fallback)", f"{e2}")

        # Also test DECIDED edge
        if decision_uid:
            try:
                result = api("POST", "/link", json_body={
                    "from_uid": agent_uid,
                    "to_uid": decision_uid,
                    "edge_type": "DECIDED",
                    "agent_id": PROJECT_ID,
                })
                r.ok("add_link DECIDED", "")
            except Exception as e:
                # Try /edge fallback
                try:
                    api("POST", "/edge", json_body={
                        "from_uid": agent_uid,
                        "to_uid": decision_uid,
                        "edge_type": "DECIDED",
                        "agent_id": PROJECT_ID,
                    })
                    r.ok("add_edge DECIDED (fallback)", "")
                except Exception as e2:
                    r.fail("add_link/edge DECIDED", f"/link: {e}, /edge: {e2}")

        # HAS_GOAL edge
        if goal_uid:
            try:
                result = api("POST", "/link", json_body={
                    "from_uid": agent_uid,
                    "to_uid": goal_uid,
                    "edge_type": "HAS_GOAL",
                    "agent_id": PROJECT_ID,
                })
                r.ok("add_link HAS_GOAL", "")
            except Exception as e:
                try:
                    api("POST", "/edge", json_body={
                        "from_uid": agent_uid,
                        "to_uid": goal_uid,
                        "edge_type": "HAS_GOAL",
                        "agent_id": PROJECT_ID,
                    })
                    r.ok("add_edge HAS_GOAL (fallback)", "")
                except Exception as e2:
                    r.fail("add_link/edge HAS_GOAL", f"/link: {e}, /edge: {e2}")
    else:
        r.fail("edge creation", f"Missing UIDs: agent={agent_uid[:8] if agent_uid else 'NONE'}, target={str(link_target)[:8] if link_target else 'NONE'}")

    # ── 12. Lifecycle ──
    print("\n[12] Lifecycle — Decay & Observation")

    try:
        result = api("POST", "/evolve", json_body={
            "action": "decay",
            "half_life_secs": 3600,
            "min_salience": 0.05,
            "agent_id": PROJECT_ID,
        })
        r.ok("decay_project_salience", f"type={type(result).__name__}")
    except Exception as e:
        r.fail("decay_project_salience", e)

    try:
        result = api("POST", "/reality/capture", json_body={
            "action": "observation",
            "label": "第3轮世界1模拟完成",
            "props": {"content": "第3轮世界1模拟完成，共42个动作", "observation_type": "simulation_event"},
            "agent_id": PROJECT_ID,
        })
        r.ok("capture_observation", f"uid={result.get('uid', 'N/A')[:16]}")
    except Exception as e:
        r.fail("capture_observation", e)

    # ── 13. Job Polling ──
    print("\n[13] Job Polling")

    job_id = created_uids.get("job_id", "")
    if job_id:
        try:
            result = api("GET", f"/jobs/{job_id}")
            r.ok("get_job", f"status={result.get('status', 'N/A')}, keys={list(result.keys())[:5]}")
        except Exception as e:
            r.fail("get_job", e)

    # ── Final Statistics ──
    print("\n[14] Final Graph State")
    try:
        nodes = api("GET", "/nodes", params={"agent": PROJECT_ID, "limit": 100})
        items = nodes.get("items", nodes) if isinstance(nodes, dict) else nodes
        if not isinstance(items, list):
            items = []
        edges = api("GET", "/edges", params={"agent": PROJECT_ID, "limit": 100})
        edge_items = edges.get("items", edges) if isinstance(edges, dict) else edges
        if not isinstance(edge_items, list):
            edge_items = []

        types = {}
        for n in items:
            nt = n.get("node_type", "?")
            types[nt] = types.get(nt, 0) + 1

        r.ok("final graph state", f"nodes={len(items)}, edges={len(edge_items)}, types={types}")
    except Exception as e:
        r.fail("final graph state", e)

    # ── Summary ──
    print(f"\nCreated UIDs:")
    for k, v in created_uids.items():
        if isinstance(v, list):
            print(f"  {k}: [{', '.join(str(x)[:16] for x in v[:3])}{'...' if len(v) > 3 else ''}]")
        else:
            print(f"  {k}: {str(v)[:24]}")

    all_passed = r.summary()

    # ── Cleanup ──
    print("\n[Cleanup] Deleting test project data...")
    try:
        offset = 0
        deleted = 0
        while True:
            result = api("GET", "/nodes", params={"agent": PROJECT_ID, "limit": 100})
            items = result.get("items", result) if isinstance(result, dict) else result
            if not isinstance(items, list) or not items:
                break
            for node in items:
                uid = node.get("uid", "")
                if uid:
                    try:
                        api("DELETE", f"/node/{uid}")
                        deleted += 1
                    except Exception:
                        pass
        print(f"  Deleted {deleted} nodes from project {PROJECT_ID}")
    except Exception as e:
        print(f"  Cleanup failed: {e}")

    return all_passed


if __name__ == "__main__":
    import sys
    success = run_tests()
    sys.exit(0 if success else 1)
