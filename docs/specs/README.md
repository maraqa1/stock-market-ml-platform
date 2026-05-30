# StockML Specification Ledger

This directory is the canonical home for platform specifications and the function map that connects those specifications to code.

## Files

- `spec_ledger.md` records implemented, planned, partial, and reconstructed specs.
- `function_registry.md` maps platform capabilities to modules, scripts, configs, migrations, and tests.

## Maintenance Rule

Every spec implementation must update this directory in the same commit, or in a documentation-only commit immediately before the next spec starts.

Each spec entry should include:

- spec number or stable identifier
- title
- status
- implementation commits
- core files
- migrations
- focused tests
- VM verification result
- paper/live safety notes

Statuses:

- `implemented`: code merged and focused tests passed on the VM.
- `implemented-pending-vm`: code merged locally/remotely, but the VM test result has not been recorded yet.
- `planned`: spec exists but implementation has not started.
- `partial`: supporting pieces exist but the spec is not complete.
- `reconstructed`: implemented historically, but the original spec text was not available and the entry was rebuilt from docs, tests, migrations, or commits.
