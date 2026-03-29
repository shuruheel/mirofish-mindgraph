"""
Cleanup script: Delete simulation-generated nodes from MindGraph

MiroFish simulations create nodes in MindGraph with agent_id=project_id.
These are namespaced separately from the original graph content, so they
can be safely identified and deleted without affecting the source graph.

Node types created by simulations:
  - Goal:        Agent persona/stance nodes (phase 4 registration)
  - Chunk:       Agent activity logs (simulation runtime)
  - Observation: Round completion events (simulation_event type)
  - Trace:       Agent interaction traces (quotes, likes, etc.)
  - Decision:    Agent decision records
  - Option:      Agent decision options
  - Session:     MindGraph session nodes
  - Hypothesis:  Simulation prediction hypothesis

Usage:
    # Dry run (default) — list what would be deleted
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962

    # Actually delete
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962 --execute

    # Delete only specific types
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962 --types Observation,Trace,Session --execute

    # Show full node details
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962 --verbose
"""

import argparse
import json
import os
import sys
import time
from collections import Counter

# Add backend directory to path
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
sys.path.insert(0, _backend_dir)

from dotenv import load_dotenv
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)


def main():
    parser = argparse.ArgumentParser(
        description='Delete simulation-generated nodes from MindGraph'
    )
    parser.add_argument(
        '--project-id', required=True,
        help='Project ID used as agent_id namespace (e.g. proj_5b2298f8c962)'
    )
    parser.add_argument(
        '--execute', action='store_true',
        help='Actually delete nodes. Without this flag, runs in dry-run mode.'
    )
    parser.add_argument(
        '--types', default=None,
        help='Comma-separated list of node types to delete (e.g. Observation,Trace,Session). '
             'Default: all simulation node types.'
    )
    parser.add_argument(
        '--verbose', action='store_true',
        help='Show full details for each node'
    )

    args = parser.parse_args()
    project_id = args.project_id
    execute = args.execute
    verbose = args.verbose
    type_filter = set(args.types.split(',')) if args.types else None

    # Initialize MindGraph client
    from app.utils.mindgraph_client import MindGraphClient
    client = MindGraphClient()

    print(f"{'=' * 60}")
    print(f"MindGraph Simulation Node Cleanup")
    print(f"{'=' * 60}")
    print(f"Project ID (agent_id): {project_id}")
    print(f"Mode: {'EXECUTE — nodes will be deleted!' if execute else 'DRY RUN — no changes will be made'}")
    if type_filter:
        print(f"Type filter: {', '.join(sorted(type_filter))}")
    print()

    # Fetch all nodes with this agent_id
    print("Fetching nodes with agent_id={} ...".format(project_id))
    nodes = client.list_all_nodes(project_id=project_id, max_items=10000)
    print(f"Found {len(nodes)} nodes\n")

    if not nodes:
        print("No simulation nodes found. Nothing to clean up.")
        return

    # Categorize nodes
    type_counts = Counter()
    nodes_to_delete = []

    for node in nodes:
        attrs = node.get("attributes") or node.get("props") or {}
        node_type = attrs.get("_type") or ""
        if not node_type:
            # Fallback to labels
            labels = node.get("labels", [])
            node_type = labels[0] if labels else "unknown"

        type_counts[node_type] += 1

        if type_filter and node_type not in type_filter:
            continue

        nodes_to_delete.append({
            "uid": node.get("uid") or node.get("uuid"),
            "name": node.get("label") or node.get("name") or attrs.get("content", "")[:80] or "?",
            "type": node_type,
            "created_at": node.get("created_at"),
        })

    # Summary
    print("Node type breakdown:")
    for t, c in type_counts.most_common():
        marker = " <-- will delete" if (not type_filter or t in type_filter) else ""
        print(f"  {t:20s}: {c:4d}{marker}")
    print()

    if type_filter:
        print(f"Nodes matching type filter: {len(nodes_to_delete)}")
    else:
        print(f"Total nodes to delete: {len(nodes_to_delete)}")
    print()

    # List nodes
    if verbose:
        for nd in nodes_to_delete:
            print(f"  [{nd['type']}] {nd['name'][:80]}")
            print(f"    uid: {nd['uid']}")
            print(f"    created_at: {nd['created_at']}")
            print()
    else:
        # Show first 10 of each type
        from collections import defaultdict
        by_type = defaultdict(list)
        for nd in nodes_to_delete:
            by_type[nd['type']].append(nd)

        for t, items in sorted(by_type.items()):
            print(f"  {t} ({len(items)}):")
            for nd in items[:5]:
                print(f"    - {nd['name'][:75]}")
            if len(items) > 5:
                print(f"    ... and {len(items) - 5} more")
            print()

    if not execute:
        print(f"{'=' * 60}")
        print("DRY RUN complete. To actually delete, add --execute flag.")
        print(f"{'=' * 60}")
        return

    # Execute deletion
    print(f"{'=' * 60}")
    print(f"Deleting {len(nodes_to_delete)} nodes...")
    print(f"{'=' * 60}")

    deleted = 0
    failed = 0
    for i, nd in enumerate(nodes_to_delete):
        uid = nd['uid']
        if not uid:
            print(f"  SKIP: no uid for {nd['name'][:50]}")
            failed += 1
            continue

        try:
            client.delete_node(uid)
            deleted += 1
            if (i + 1) % 50 == 0 or (i + 1) == len(nodes_to_delete):
                print(f"  Progress: {i + 1}/{len(nodes_to_delete)} "
                      f"(deleted={deleted}, failed={failed})")
        except Exception as e:
            failed += 1
            print(f"  FAILED: [{nd['type']}] {nd['name'][:50]} — {e}")

        # Rate limiting: small pause every 20 deletions
        if (i + 1) % 20 == 0:
            time.sleep(0.5)

    print()
    print(f"{'=' * 60}")
    print(f"Cleanup complete: {deleted} deleted, {failed} failed")
    print(f"{'=' * 60}")

    client.close()


if __name__ == '__main__':
    main()
