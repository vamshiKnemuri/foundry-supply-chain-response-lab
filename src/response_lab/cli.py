"""CLI entrypoint for the local lab."""

import argparse
import json
from pathlib import Path

from .engine import LabError, ResponseLab


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-first supply chain response lab")
    parser.add_argument("command", choices=["serve", "investigate", "evaluate"])
    parser.add_argument("disruption", nargs="?", default="DIS-001")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", action="store_true", help="Use optional OpenAI candidate ranking")
    parser.add_argument("--db", type=Path, default=Path(".local/response_lab.db"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    ranker = None
    if args.model:
        from .model import OpenAIRanker
        ranker = OpenAIRanker()
    lab = ResponseLab(root / "data" / "scenarios.json", args.db, ranker)
    try:
        if args.command == "serve":
            from .server import serve
            serve(lab, root / "web", args.port)
        elif args.command == "investigate":
            print(json.dumps(lab.investigate(args.disruption), indent=2))
        else:
            cases = []
            for disruption in lab.ontology.disruptions():
                try:
                    result = lab.investigate(disruption["id"])
                    cases.append({"id": disruption["id"], "outcome": "reviewable" if result["gate"]["allowed_for_review"] else "blocked"})
                except LabError:
                    cases.append({"id": disruption["id"], "outcome": "no_feasible_plan"})
            print(json.dumps(cases, indent=2))
    except LabError as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()

