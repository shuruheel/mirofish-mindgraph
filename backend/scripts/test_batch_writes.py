"""
Live test: Verify batch write paths work correctly with MindGraph API.

Tests the same batch_create patterns used by:
1. GraphMemoryUpdater._send_batch_activities (Journal + Decision + Option nodes, then edges)
2. SimulationManager Phase 4 (Entity + Journal nodes, then HAS_JOURNAL edges)

Uses a dedicated agent_id so all test nodes can be cleaned up via batch_delete_nodes.
"""

import os
import sys
import time

_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)

from app.utils.mindgraph_client import MindGraphClient


def main():
    AGENT_ID = "test_batch_writes"
    client = MindGraphClient()
    errors = []

    print("=" * 60)
    print("Live Test: Batch Write Paths")
    print("=" * 60)
    print(f"agent_id: {AGENT_ID}")
    print()

    # ── Test 1: Batch create nodes (simulates _send_batch_activities) ──
    print("Test 1: batch_create nodes (Journal + Decision + Option)...")
    try:
        result = client.batch_create(nodes=[
            {
                "label": "TestAgent: This is a test journal post",
                "props": {
                    "_type": "Journal",
                    "content": "TestAgent: This is a test journal post about batch writes",
                    "journal_type": "simulation_post",
                    "tags": ["twitter", "CREATE_POST", "round_1"],
                },
                "agent_id": AGENT_ID,
            },
            {
                "label": "TestAgent2: Another test journal",
                "props": {
                    "_type": "Journal",
                    "content": "TestAgent2: Another test journal entry for verification",
                    "journal_type": "simulation_post",
                    "tags": ["reddit", "CREATE_POST", "round_1"],
                },
                "agent_id": AGENT_ID,
            },
            {
                "label": "TestAgent decided to publicly comment",
                "props": {
                    "_type": "Decision",
                    "description": "TestAgent decided to publicly comment",
                    "rationale": "Agent stance: supportive, sentiment: 0.5",
                },
                "agent_id": AGENT_ID,
            },
            {
                "label": "Test option content",
                "props": {
                    "_type": "Option",
                    "description": "This is the content of the decision option",
                },
                "agent_id": AGENT_ID,
            },
        ])
        node_uids = result.get("node_uids", [])
        nodes_added = result.get("nodes_added", 0)
        batch_errors = result.get("errors", [])
        print(f"  ✓ nodes_added={nodes_added}, uids={len(node_uids)}")
        if batch_errors:
            print(f"  ⚠ errors: {batch_errors}")
            errors.append(f"Test 1 had errors: {batch_errors}")
        if len(node_uids) != 4:
            errors.append(f"Test 1: expected 4 UIDs, got {len(node_uids)}")
            print(f"  ✗ Expected 4 UIDs, got {len(node_uids)}")
        else:
            print(f"  UIDs: {node_uids}")
    except Exception as e:
        errors.append(f"Test 1 failed: {e}")
        print(f"  ✗ FAILED: {e}")
        node_uids = []

    # ── Test 2: Batch create edges (simulates AUTHORED + DECIDED + HasOption) ──
    print()
    print("Test 2: batch_create edges (AUTHORED + DECIDED + HasOption)...")
    if len(node_uids) >= 4:
        journal1_uid, journal2_uid, decision_uid, option_uid = node_uids[:4]
        try:
            result = client.batch_create(edges=[
                # Simulate AUTHORED edges (normally Agent→Journal)
                # Using journal→decision as proxy since we don't have real Agent nodes
                {"from_uid": journal1_uid, "to_uid": decision_uid, "edge_type": "TEST_AUTHORED"},
                {"from_uid": journal2_uid, "to_uid": decision_uid, "edge_type": "TEST_AUTHORED"},
                # Decision → Option
                {"from_uid": decision_uid, "to_uid": option_uid, "edge_type": "HasOption"},
            ])
            edges_added = result.get("edges_added", 0)
            batch_errors = result.get("errors", [])
            print(f"  ✓ edges_added={edges_added}")
            if batch_errors:
                print(f"  ⚠ errors: {batch_errors}")
                errors.append(f"Test 2 had errors: {batch_errors}")
        except Exception as e:
            errors.append(f"Test 2 failed: {e}")
            print(f"  ✗ FAILED: {e}")
    else:
        print("  ⊘ Skipped (Test 1 didn't return enough UIDs)")

    # ── Test 3: Batch create Entity + Journal (simulates Phase 4) ──
    print()
    print("Test 3: batch_create nodes (Agent + Journal — Phase 4 pattern)...")
    phase4_uids = []
    try:
        result = client.batch_create(nodes=[
            {
                "label": "Test Entity Alpha",
                "props": {
                    "_type": "Agent",
                    "original_entity_type": "Person",
                    "stance": "supportive",
                    "sentiment_bias": 0.7,
                    "influence_weight": 1.2,
                    "simulation_id": "test_sim_001",
                    "summary": "Person: supportive stance, sentiment=0.7",
                },
                "agent_id": AGENT_ID,
            },
            {
                "label": "Test Entity Alpha holds a supportive stance",
                "props": {
                    "_type": "Journal",
                    "content": "Test Entity Alpha holds a supportive stance with sentiment bias 0.70 and influence weight 1.20",
                    "journal_type": "stance",
                    "tags": ["supportive", "Person"],
                },
                "agent_id": AGENT_ID,
            },
            {
                "label": "Test Entity Beta",
                "props": {
                    "_type": "Agent",
                    "original_entity_type": "Organization",
                    "stance": "opposing",
                    "sentiment_bias": -0.5,
                    "influence_weight": 0.8,
                    "simulation_id": "test_sim_001",
                    "summary": "Organization: opposing stance, sentiment=-0.5",
                },
                "agent_id": AGENT_ID,
            },
        ])
        phase4_uids = result.get("node_uids", [])
        nodes_added = result.get("nodes_added", 0)
        batch_errors = result.get("errors", [])
        print(f"  ✓ nodes_added={nodes_added}, uids={len(phase4_uids)}")
        if batch_errors:
            print(f"  ⚠ errors: {batch_errors}")
            errors.append(f"Test 3 had errors: {batch_errors}")
        if len(phase4_uids) != 3:
            errors.append(f"Test 3: expected 3 UIDs, got {len(phase4_uids)}")
    except Exception as e:
        errors.append(f"Test 3 failed: {e}")
        print(f"  ✗ FAILED: {e}")

    # ── Test 4: Batch create HAS_JOURNAL edge (Phase 4 edge pattern) ──
    print()
    print("Test 4: batch_create edges (HAS_JOURNAL — Phase 4 pattern)...")
    if len(phase4_uids) >= 2:
        entity_uid = phase4_uids[0]
        journal_uid = phase4_uids[1]
        try:
            result = client.batch_create(edges=[
                {"from_uid": entity_uid, "to_uid": journal_uid, "edge_type": "HAS_JOURNAL"},
            ])
            edges_added = result.get("edges_added", 0)
            print(f"  ✓ edges_added={edges_added}")
        except Exception as e:
            errors.append(f"Test 4 failed: {e}")
            print(f"  ✗ FAILED: {e}")
    else:
        print("  ⊘ Skipped (Test 3 didn't return enough UIDs)")

    # ── Test 5: Session trace (simulates social action trace write) ──
    print()
    print("Test 5: open_session + trace_session + close_session...")
    try:
        session_uid = client.open_session(
            project_id=AGENT_ID,
            session_name="Test batch write session",
        )
        print(f"  ✓ Session opened: {session_uid}")

        client.trace_session(
            session_uid=session_uid,
            content="TestAgent: liked a post\nTestAgent2: followed TestAgent",
            project_id=AGENT_ID,
            trace_type="simulation_activity",
        )
        print(f"  ✓ Trace written")

        client.close_session(session_uid=session_uid, project_id=AGENT_ID)
        print(f"  ✓ Session closed")
    except Exception as e:
        errors.append(f"Test 5 failed: {e}")
        print(f"  ✗ FAILED: {e}")

    # ── Test 6: Observation node via batch (simulates record_round_end) ──
    print()
    print("Test 6: batch_create Observation node (round end pattern)...")
    try:
        result = client.batch_create(nodes=[{
            "label": "Round 1 World 1 simulation completed, 10 actions",
            "props": {
                "_type": "Observation",
                "content": "Round 1 World 1 simulation completed, 10 actions",
                "observation_type": "simulation_event",
            },
            "agent_id": AGENT_ID,
        }])
        print(f"  ✓ nodes_added={result.get('nodes_added', 0)}")
    except Exception as e:
        errors.append(f"Test 6 failed: {e}")
        print(f"  ✗ FAILED: {e}")

    # ── Cleanup: Delete all test nodes ──
    print()
    print("Cleanup: batch_delete_nodes...")
    try:
        result = client.batch_delete_nodes(
            agent_id=AGENT_ID,
            reason="test_cleanup",
        )
        tombstoned = result.get("nodes_tombstoned", 0)
        edges_tombstoned = result.get("edges_tombstoned", 0)
        print(f"  ✓ Deleted: {tombstoned} nodes, {edges_tombstoned} edges")
    except Exception as e:
        errors.append(f"Cleanup failed: {e}")
        print(f"  ✗ FAILED: {e}")

    # ── Summary ──
    print()
    print("=" * 60)
    if errors:
        print(f"RESULT: {len(errors)} error(s)")
        for err in errors:
            print(f"  - {err}")
    else:
        print("RESULT: All tests passed")
    print("=" * 60)

    client.close()


if __name__ == "__main__":
    main()
