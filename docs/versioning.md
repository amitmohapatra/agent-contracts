# Versioning and compatibility

## What goes with what

The packages move together. A combination is "supported" when a test run exercised it, not
when it merely installs. The pins below are copied from each package's `pyproject.toml` on
`main`.

| Package | Version | Requires `trellis-contracts` | Notes |
|---|---|---|---|
| `trellis-contracts` | **0.6.1** | — | this package |
| `trellis-harness` (agent-harness) | **0.4.0** | `>=0.6.0,<0.7` | builds and reads every record; installs the sibling checkout (`../agent-contracts`) in development |
| `agent-runs` and `trellis-runs` (its SDK) | **0.4.0** | `>=0.6.1,<0.7` | the service's create body subclasses `RunStart`; its OpenAPI document embeds these models |
| `trellis-memory` (Memory Service SDK) | **0.4.0** | not a dependency | sends a `Feedback` as it is; its exceptions are classified by class name |
| `trellis-memory-service` (agent-memory-service) | **0.3.0** | not a dependency | `POST /v1/feedback` takes the `Feedback` shape; `scope_fields()` returns its `Scope` keywords |
| `bifrost-sdk` | **0.3.0** | not a dependency | independent; `classify()` maps its exceptions by class name |
| `pydantic` | `>=2.13,<3` | — | the only runtime dependency |
| Python | `>=3.12` | — | `StrEnum`, PEP 695 generics |

## The version rules

The package is pre-1.0, and every consumer is a sibling repo that moves in the same change.

* **Minor (0.x.0)** for anything a consumer must change for: a removed name or port, a field
  that changes meaning, a new required field. 0.4.0 (contracts v3), 0.5.0 (the `RunStore`
  port removed) and 0.6.0 (interrupts v2) were minor.
* **Patch (0.x.y)** for optional fields with a default and new helpers. 0.5.1 (working-time
  limit, agent version) and 0.6.1 (schedules carry queue order and metadata) were patches.
* **No aliases.** A removed name is gone in the release that removes it; the ADR says what
  replaces it.

A written record refuses unknown fields (`extra="forbid"`), so a record carrying a field added
in a patch is refused by a reader on an older patch. A producer therefore pins at least the
version whose fields it sends, and a consumer reading records back (`RunRecord`, `Schedule`,
`AgentCard`) ignores fields it does not know. That is why agent-runs, which stores the
schedule fields 0.6.1 added, pins `>=0.6.1`.

## Where changes are recorded

* [CHANGELOG.md](../CHANGELOG.md): what each version changed.
* [adr/](adr/README.md): why, one record per decision, with the version it shipped in.
