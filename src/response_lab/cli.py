"""CLI entrypoint for the local lab."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .engine import LabError, NoFeasiblePlan, ResponseLab


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-first supply chain response lab")
    parser.add_argument("command", choices=["serve", "investigate", "evaluate"])
    parser.add_argument("disruption", nargs="?", default="DIS-001")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", action="store_true", help="Use optional OpenAI candidate ranking")
    parser.add_argument("--model-name", default="gpt-4o-mini", help="Structured-output compatible model used with --model")
    parser.add_argument("--db", type=Path, default=Path(".local/response_lab.db"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent / "assets"
    ranker = None
    if args.model:
        from .model import OpenAIRanker
        ranker = OpenAIRanker(args.model_name)
    try:
        if not 1 <= args.port <= 65535:
            raise LabError("Port must be between 1 and 65535")
        if args.command == "evaluate":
            if args.model:
                raise LabError("Evaluation uses the deterministic planner; omit --model")
            expected = {"DIS-001": "reviewable", "DIS-002": "no_feasible_plan", "DIS-003": "blocked"}
            with TemporaryDirectory() as temporary:
                evaluation_lab = ResponseLab(root / "data" / "scenarios.json", Path(temporary) / "evaluation.db")
                cases = []
                for disruption in evaluation_lab.ontology.disruptions():
                    try:
                        result = evaluation_lab.investigate(disruption["id"])
                        outcome = "reviewable" if result["gate"]["allowed_for_review"] else "blocked"
                    except NoFeasiblePlan:
                        outcome = "no_feasible_plan"
                    cases.append({"id": disruption["id"], "outcome": outcome})
            if {case["id"]: case["outcome"] for case in cases} != expected:
                raise LabError(f"Scenario evaluation failed: {cases}")
            print(json.dumps(cases, indent=2))
            return
        lab = ResponseLab(root / "data" / "scenarios.json", args.db, ranker)
        if args.command == "serve":
            from .server import serve
            serve(lab, root / "web", args.port)
        elif args.command == "investigate":
            print(json.dumps(lab.investigate(args.disruption), indent=2))
    except (LabError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
