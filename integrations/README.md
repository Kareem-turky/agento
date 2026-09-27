# integrations/

Connection to arbitrary external business systems.

The code lives in the API package: `apps/api/app/integrations/commerce/` holds the
product-owned `CommerceIntegration` contract (read-only), and `mock/` holds a
deterministic mock provider with its adapter. The core depends only on the contract,
never on a specific adapter. See the root README ("Commerce integrations").

`contracts/` and `adapters/` here remain empty placeholders.
