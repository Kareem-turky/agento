"""The ONE Product-owned statement of the Product schema revision this build serves.

Readiness and System Status compare ``product.alembic_version`` with it; nothing imports
an Alembic migration module at runtime. A test pins it to the head of
``apps/api/migrations/versions``, so a new migration cannot ship without updating it.
"""

EXPECTED_PRODUCT_SCHEMA_REVISION = "0009"
