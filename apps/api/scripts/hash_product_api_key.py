"""Print the SHA-256 to configure for a Product API key. Offline; stores nothing.

The raw key is read from STDIN, never from the command line (so it never lands in
shell history or process listings):

    uv run python apps/api/scripts/hash_product_api_key.py          # prompts, no echo
    <secret-source> | uv run python apps/api/scripts/hash_product_api_key.py

It must be 32-256 printable ASCII characters with no whitespace. Only the lowercase
hex SHA-256 is printed; put it in APP_PRODUCT_API_KEYS (``key_sha256``) and hand the
raw key to the client. An invalid key prints a generic error (never the key) and exits 2.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api (the ``app`` package)

from app.auth.keys import InvalidApiKeyError, hash_api_key  # noqa: E402


def read_raw_key() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Product API key (input hidden): ")
    data = sys.stdin.read()
    # Tolerate exactly one trailing line ending from `echo`/files; nothing else is trimmed.
    if data.endswith("\r\n"):
        return data[:-2]
    if data.endswith("\n"):
        return data[:-1]
    return data


def main(argv: list[str]) -> int:
    if argv:
        # Never accept the secret (or anything else) as an argument.
        print("usage: hash_product_api_key.py  (reads the key from stdin)", file=sys.stderr)
        return 2
    try:
        digest = hash_api_key(read_raw_key())
    except InvalidApiKeyError:
        print(
            "invalid key: need 32-256 printable ASCII characters, no whitespace",
            file=sys.stderr,
        )
        return 2
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
