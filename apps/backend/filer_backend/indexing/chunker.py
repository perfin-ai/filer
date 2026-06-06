"""Lightweight recursive text chunker.

Token counts are approximated by characters (~4 chars/token), so the defaults
(~512 tokens / ~64 overlap) map to ~2000 / ~256 characters. No heavy framework
dependency.
"""

import re

_PARA = re.compile(r"\n\s*\n")


def _split_long(text: str, budget: int) -> list[str]:
    """Hard-split an over-budget block on word boundaries."""
    words = text.split()
    out: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > budget:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(
    text: str, target_chars: int = 2000, overlap_chars: int = 256
) -> list[str]:
    text = text.strip()
    if not text:
        return []

    blocks: list[str] = []
    cur = ""
    for para in (p.strip() for p in _PARA.split(text) if p.strip()):
        if len(para) > target_chars:
            if cur:
                blocks.append(cur)
                cur = ""
            blocks.extend(_split_long(para, target_chars))
        elif len(cur) + 1 + len(para) <= target_chars:
            cur = f"{cur}\n{para}".strip()
        else:
            blocks.append(cur)
            cur = para
    if cur:
        blocks.append(cur)

    if overlap_chars <= 0 or len(blocks) <= 1:
        return blocks
    # Prepend a tail of the previous block to each subsequent block.
    out = [blocks[0]]
    for prev, block in zip(blocks, blocks[1:]):
        out.append((prev[-overlap_chars:] + "\n" + block).strip())
    return out
