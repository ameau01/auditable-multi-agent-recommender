# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Design] Add initial design docs.
### Added
- Created project skeletion with `pyproject.toml`, `requirement.txt`
- Added MIT standard license 
- Add project architecture and design documentation: `ARCHITECTURE.md` and `README.md`
- Add supporting design doc under `docs\` folder

### Documentation
- `ARCHITECTURE.md` and `README.md`

## [DataSet and EvalSet] Update repo with dataset and design doc.
### Changed
- Updated eval-set.md
- Doc refactory on agent.md, ARCHITECTURE.md, decisions.md

### Added
- Added src/evaluator code 
- Added unit test and mock fixture in src/evaluator code.
- Added integration test to run 18 scenarios of golden eval-set.
- Added Eval-set folders

### Documentation
- `Eval-set.md`, `Agend.md`, `ARCHITECTURE.md`, `decisions.md`

## [Sample Dataset and sample-run]
### Changed
- update eval-set.md
- update docs/eval-set.md
- update eval-set/README.md

### Added
- Added example dataset (scenario 06, 15, 17)
- Added sample-run for scneario 06, 15, 17

## [MCP Server]  Add implementation of MCP sever.

### Added
- added src/mcp-server
- added tests/integration/test_mcp_server.py
- added tests/unit/mcp_server

### Documentation
- Update docs/mcp-server.md


## [Pydantic model] refactory
### Added
- src/models: Added new pydantic model as data contract 
- Treating telemetry model as input data contract to agentic system.
- Treating recommendation model as output data contract from agentic system.

### Changed.
- refactory src/renderer to use recomendation pydantic model
- refactory src/evaluator to use recommendation pydantic model
- refactory src/mcp_server to use telemetry pydantic model

### Documentation
- `docs/eval-set.md`, `docs/eval-set.md`, `docs/mcp-server.md`

## [AUDIT TRAIL] implementation
### Added
- initialize with SQL Lite from os/.env of (AUDIT_DB_PATH=.audit_db/audit.db)
- add method and schema for two table audit trails 
- add source code for src/audit 
- add unit tests for audit-trail


### Changed
- update src/models/enum.py for common enum.

### Documentations
- `docs/audit-trail.md`

## [ACTION AND INPUT HARNESS] implementation
### Added 
- src/harnesses

### Changed
- update src/audit to have new table for harness audit trail

### Documentations
- `docs/audit-trail.md`, `docs/harnesses.md`

## [FIX evidence and reason harness] BUG + BEGIN langgraph coding.

### Changed
- add proper evidence for every decision

### Added
- added proper langgraph start /end node
- added sysem mapper node and supervisor node 

### Documentation
- `docs/audit-trail.md`, `docs/harnesses.md`, `ARCHITECTURE.md`

## [Orchestration Harness] implementation

### Changed.
- update enums and audit.py

### Added.
- Add orchestration.py the fourth harness.

### Documentation
`docs/harnesses.md`
