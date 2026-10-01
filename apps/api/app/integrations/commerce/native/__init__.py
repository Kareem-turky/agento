"""The Product-owned native commerce adapter (Task 031).

Not an external provider: it reads the Product's OWN canonical commerce state (the
PostgreSQL store, ``app.persistence.PostgresCommerceStore``) through the
``app.commerce.store.CommerceStoreReader`` port. No provider ids, SDK, HTTP transport,
credentials or external endpoint exist here. Not yet registered as a deployment
business backend.
"""

from app.integrations.commerce.native.adapter import NATIVE_DESCRIPTOR, NativeCommerceAdapter

__all__ = ["NATIVE_DESCRIPTOR", "NativeCommerceAdapter"]
