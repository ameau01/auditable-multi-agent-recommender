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

