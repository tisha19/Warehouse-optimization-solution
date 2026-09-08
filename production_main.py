"""Production entry point for the NVIDIA agentic warehouse workflow."""

import argparse
import json

from agents.workflow import ProductionWarehouseWorkflow
from services.config import ProductionConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the production warehouse DeepAgent workflow")
    parser.add_argument("--goal", default="Reduce picker travel and optimize warehouse space utilization")
    parser.add_argument("--actor", required=True, help="Authenticated planner or service identity")
    parser.add_argument("--write-back-approval", help="Approved approval_id to write moves to the WMS")
    args = parser.parse_args()

    workflow = ProductionWarehouseWorkflow(ProductionConfig.from_env())
    if args.write_back_approval:
        result = workflow.write_back(args.write_back_approval, args.actor)
    else:
        result = workflow.run(args.goal, args.actor)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
