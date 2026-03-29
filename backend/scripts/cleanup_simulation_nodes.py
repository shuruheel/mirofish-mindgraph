"""
Cleanup script: Delete simulation-generated nodes from MindGraph

MiroFish simulations create nodes in MindGraph with agent_id=project_id.
These are namespaced separately from the original graph content, so they
can be safely identified and deleted without affecting the source graph.

Node types created by simulations:
  - Journal:     Agent posts and stance records (Memory layer)
  - Trace:       Agent interaction traces (likes, reposts, etc.)
  - Decision:    Agent decision records (Intent layer)
  - Option:      Agent decision options
  - Observation: Round completion events
  - Session:     MindGraph session nodes
  - Hypothesis:  Simulation prediction hypothesis
  - Entity:      SimulationAgent nodes (Phase 4 registration)

Usage:
    # Delete all simulation nodes for a project (single API call)
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962

    # Delete only specific node types
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962 --types Journal,Trace

    # Dry run — show what the batch delete call would target
    cd backend && uv run python scripts/cleanup_simulation_nodes.py --project-id proj_5b2298f8c962 --dry-run
"""

import argparse
import os
import sys

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
        '--types', default=None,
        help='Comma-separated list of node types to delete (e.g. Journal,Trace). '
             'Default: all nodes for the agent_id.'
    )
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Show what would be deleted without actually deleting.'
    )

    args = parser.parse_args()
    project_id = args.project_id
    dry_run = args.dry_run
    type_filter = args.types.split(',') if args.types else None

    from app.utils.mindgraph_client import MindGraphClient
    client = MindGraphClient()

    print(f"{'=' * 60}")
    print(f"MindGraph Simulation Node Cleanup")
    print(f"{'=' * 60}")
    print(f"Project ID (agent_id): {project_id}")
    if type_filter:
        print(f"Type filter: {', '.join(type_filter)}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print()

    # Build filter
    node_filter = None
    if type_filter:
        node_filter = {"node_types": type_filter}

    if dry_run:
        print("Would call: POST /nodes/delete")
        print(f"  agent_id: {project_id}")
        if node_filter:
            print(f"  filter: {node_filter}")
        print()
        print("DRY RUN complete. Remove --dry-run to execute.")
        client.close()
        return

    # Execute batch delete
    print(f"Deleting nodes with agent_id={project_id}...")
    if node_filter:
        print(f"  filter: {node_filter}")
    print()

    result = client.batch_delete_nodes(
        agent_id=project_id,
        filter=node_filter,
        reason="simulation_cleanup",
    )

    print(f"{'=' * 60}")
    print(f"Cleanup complete:")
    print(f"  Nodes tombstoned:  {result.get('nodes_tombstoned', 0)}")
    print(f"  Edges tombstoned:  {result.get('edges_tombstoned', 0)}")
    print(f"{'=' * 60}")

    client.close()


if __name__ == '__main__':
    main()
