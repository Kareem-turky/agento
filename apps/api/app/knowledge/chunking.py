"""Deterministic Product-owned chunking (Task 035): no tokenizer, no model.

    normalized body
      -> blocks: paragraphs separated by blank lines; a Markdown heading line ("#" ...)
         always starts a new block
      -> blocks longer than MAX_CHUNK_CHARS are split at the last whitespace before the
         limit (a hard cut only when a run has no whitespace)
      -> blocks are packed IN ORDER into chunks of at most MAX_CHUNK_CHARS, joined by a
         blank line; a heading starts a new chunk whenever the current one is non-empty
         and adding it would exceed half the chunk size (keeps sections together)

The same body always yields the same chunks (content, order, offsets and hashes).
Offsets are character positions of the chunk's first and last block in the body.
"""

import hashlib
from dataclasses import dataclass

from app.knowledge.errors import KnowledgeValidationError, KnowledgeValidationReason
from app.knowledge.limits import MAX_CHUNK_CHARS, MAX_CHUNKS_PER_VERSION


@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int
    content: str
    start: int  # character offset (inclusive) in the normalized body
    end: int  # character offset (exclusive)
    content_hash: str


@dataclass(frozen=True, slots=True)
class _Block:
    text: str
    start: int
    end: int
    heading: bool


def _blocks(body: str) -> list[_Block]:
    blocks: list[_Block] = []
    lines: list[tuple[str, int]] = []

    def flush() -> None:
        if lines:
            text = "\n".join(line for line, _ in lines).strip()
            if text:
                start = lines[0][1]
                blocks.append(_Block(text, start, start + len("\n".join(part for part, _ in lines)),
                                     text.startswith("#")))  # fmt: skip
            lines.clear()

    offset = 0
    for line in body.split("\n"):
        if not line.strip():
            flush()
        elif line.lstrip().startswith("#"):
            flush()
            lines.append((line, offset))
            flush()  # a heading is its own block (packed with what follows)
        else:
            lines.append((line, offset))
        offset += len(line) + 1
    flush()
    return blocks


def _split(block: _Block, limit: int) -> list[_Block]:
    pieces: list[_Block] = []
    text, start = block.text, block.start
    while len(text) > limit:
        cut = text.rfind(" ", 0, limit + 1)
        newline = text.rfind("\n", 0, limit + 1)
        cut = max(cut, newline)
        if cut <= 0:
            cut = limit  # no whitespace: deterministic hard cut
        head = text[:cut].rstrip()
        pieces.append(_Block(head, start, start + cut, block.heading and not pieces))
        rest = text[cut:]
        stripped = rest.lstrip()
        start += cut + (len(rest) - len(stripped))
        text = stripped
    if text:
        pieces.append(_Block(text, start, start + len(text), block.heading and not pieces))
    return pieces


def chunk_text(body: str, *, limit: int = MAX_CHUNK_CHARS) -> tuple[TextChunk, ...]:
    """Deterministic chunks of an already-validated body (see the module docstring)."""
    blocks = [piece for block in _blocks(body) for piece in _split(block, limit)]
    chunks: list[tuple[str, int, int]] = []
    current: list[_Block] = []

    def size(parts: list[_Block]) -> int:
        return sum(len(p.text) for p in parts) + 2 * max(len(parts) - 1, 0)

    for block in blocks:
        candidate = current + [block]
        starts_section = block.heading and current and size(candidate) > limit // 2
        if current and (size(candidate) > limit or starts_section):
            chunks.append(("\n\n".join(p.text for p in current), current[0].start,
                           current[-1].end))  # fmt: skip
            current = [block]
        else:
            current = candidate
    if current:
        chunks.append(("\n\n".join(p.text for p in current), current[0].start, current[-1].end))
    if len(chunks) > MAX_CHUNKS_PER_VERSION:
        raise KnowledgeValidationError(KnowledgeValidationReason.TOO_MANY_CHUNKS)
    return tuple(
        TextChunk(index=i, content=content, start=start, end=end,
                  content_hash=hashlib.sha256(content.encode()).hexdigest())
        for i, (content, start, end) in enumerate(chunks)
    )  # fmt: skip
