# integrations/

Connection to arbitrary external business systems.

- `contracts/` — system-neutral interfaces the core depends on.
- `adapters/` — implementations of those contracts for specific external systems.

The core depends only on contracts, never on a specific adapter. Empty placeholder for now.
