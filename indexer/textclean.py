"""
Text cleaning shared by the indexer, the jobs that write dynamic known facts and the API.

Standard library only, so that the cron jobs and the API can use it without loading the
indexing stack.
"""

import unicodedata


def strip_invisible_chars(text: str) -> str:
    """Removes the characters a reader cannot see but a model reads: format controls (zero-width,
    bidirectional marks, the 'tag' block U+E0000 that can spell a hidden message in ASCII), private
    use and unassigned code points, and control characters other than newline and tab."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        ch for ch in text
        if ch in "\n\t" or (unicodedata.category(ch) not in ("Cf", "Co", "Cs", "Cn", "Cc") and ch != "﻿")
    )
