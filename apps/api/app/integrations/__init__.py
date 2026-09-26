"""Product-owned integration boundary.

External systems are reached only through product contracts. Adapters translate
provider data into the canonical commerce domain; provider models never leave the
adapter. Dependency direction: integrations -> canonical domain, never the reverse.
"""
