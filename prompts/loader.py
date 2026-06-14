import os
from functools import lru_cache

PROMPTS_DIR = os.path.dirname(__file__)


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load a plain-text prompt template from prompts/<name>.txt.

    Callers use str.format(...) to fill {placeholders}. Results are cached.
    """
    path = os.path.join(PROMPTS_DIR, f"{name}.txt")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()
