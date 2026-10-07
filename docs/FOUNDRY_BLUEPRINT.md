# Foundry integration blueprint

This guide is a **proposed** Foundry deployment path. It has not been tested in a tenant.

## 1. Ontology mapping

Create object types `Disruption`, `Shipment`, `Supplier`, `InventoryLot`, `Route`, `ResponseProposal`, and `DecisionEvent`, each with stable primary keys. Link `Disruption → Shipment → Supplier`, `Shipment → InventoryLot` by SKU, and `InventoryLot → Route` by facility. Keep access control at the object and property level; do not expose sensitive supplier fields to the agent by default. Map the local sample fields in `src/response_lab/assets/data/scenarios.json` to real datasets and object properties after review.

## 2. Agent workflow

Implement six read tools as Ontology SDK object reads/searches or AIP Logic tool blocks: disruption, shipment, supplier, inventory, route, and policy. AIP Logic can compose LLM and tool blocks, then write ontology edits through a published Logic function invoked by an action or automation. Keep proposal generation distinct from the action. The model should select from prevalidated candidates; a deterministic function must recompute quantity, arrival, cost, available stock, and permissions.

## 3. Approval and action

Create a `SubmitResponseProposal` action that writes only a pending `ResponseProposal`. Present evidence and policy checks in a Workshop application. Configure a separate `ApproveResponseProposal` action restricted to an operations approver role. Configure `ExecuteResponseProposal` to recheck stock and policy immediately before reserving inventory. Use a unique proposal key and idempotency design. Log action results and errors as `DecisionEvent` objects. Treat action API `validation.result == VALID` as a required success check, not just HTTP 200.

## 4. Evaluation and rollout

Recreate the three supplied cases in AIP Evals, including insufficient stock and over-budget routes. Add prompt-injection tests in free-text disruption notes. Run ontology-edit simulations before any production action. Start with read-only access, then use a test ontology and scoped credentials. Production deployment requires Foundry-specific access reviews, action permissions, data protection, monitoring, and an operator runbook.

## API mapping

The inactive `FoundryClient` implements object get, object search, and apply action request shapes. It defaults to `allow_write=False`. An integration would need tenant URL, ontology API name, OAuth access token, object/action API names, schema mapping, pagination, action validation, and tenant testing. Never place tokens in the repository.

Official references: [Ontology SDK](https://www.palantir.com/docs/foundry/ontology-sdk), [AIP Logic concepts](https://www.palantir.com/docs/foundry/logic/core-concepts), [AIP Evals for ontology edits](https://www.palantir.com/docs/foundry/aip-evals/ontology-edits), [search objects API](https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/ontology-objects/search-objects), [apply action API](https://www.palantir.com/docs/foundry/api/ontologies-v2-resources/actions/apply-action).
