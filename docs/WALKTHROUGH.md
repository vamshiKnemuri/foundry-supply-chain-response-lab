# Two-minute demo walkthrough

This walkthrough uses synthetic data. It runs on your computer without a Foundry workspace or an AI API key.

## Start with a fresh demo

From the repository root, install the project and start the dashboard:

```bash
python -m pip install -e .
response-lab serve --db .local/walkthrough.db
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). If you have used that database file before, choose a different filename for a fresh walkthrough.

## Follow the decision

| Time | Show in the dashboard | What to say |
| --- | --- | --- |
| 0:00–0:20 | In the **Disruption queue**, choose **Investigate** on `DIS-001`. | “A supplier interruption has delayed 120 sensors. The lab reads linked shipment, supplier, inventory, route, and policy records before proposing a response.” |
| 0:20–0:45 | In **Agent investigation**, show the read tool trace, grounding evidence, policy checks, and the **Ready for human review** result. | “The local planner recommends 120 units from the Dallas lot via the Chicago route. It estimates nine days recovered and $5,160 in incremental cost. The proposal includes its source fields and passes the configured policy.” |
| 0:45–1:15 | Click **Submit for approval**, enter a demo approver name, and click **Approve**. | “Submitting records a pending proposal. A separate approval step is required before the simulated action is available.” |
| 1:15–1:35 | Click **Execute local simulation**. Show the case marked **Resolved in simulation** and the **Decision ledger** with integrity **VALID**. | “Execution updates a local SQLite inventory balance and writes a hash-chained audit event. It does not reserve real inventory or call Foundry.” |
| 1:35–2:00 | Investigate `DIS-002` and `DIS-003`. | “Insufficient stock stops the pump case. The chip alternative is blocked because its cost exceeds the $10,000 policy. These cases show that the system can decline an unsafe response.” |

## What runs today

- A local dashboard and API backed by synthetic ontology-like objects and SQLite.
- Six read-only investigation tools, deterministic candidate selection, recalculated policy checks, a named demo approval step, simulated execution, and a verifiable local audit chain.
- Automated tests and a three-case evaluation in GitHub Actions. Run the evaluation yourself with `response-lab evaluate`.
- Optional OpenAI candidate ranking when you supply your own API key and use `response-lab investigate DIS-001 --model`. The standard walkthrough does not call a model.

## What remains a blueprint

- The Foundry ontology, AIP workflow, actions, permissions, and deployment described in the [integration blueprint](FOUNDRY_BLUEPRINT.md) have not been implemented or tested in a Foundry tenant.
- The local approver name is not an authenticated identity. The local audit chain is not a production audit service. See [security and deployment limits](SECURITY.md).

**Portfolio description:** “Built a local supply-chain response lab that links operational records, generates evidence-backed proposals, enforces policy and human approval, and simulates auditable actions. Designed a Foundry integration path; no live Foundry deployment is claimed.”
