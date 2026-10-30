"""Central configuration. Fixes: absolute DB path, .env loaded by absolute path, Groq key/model wiring."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # real env vars win over the file

_db = Path(os.getenv("DB_PATH", "database.db"))
DB_PATH: Path = _db if _db.is_absolute() else (BASE_DIR / _db)

# --- LLM wiring -------------------------------------------------------------
# LiteLLM reads GROQ_API_KEY for "groq/..." models. Many people store a Groq key
# (starts with "gsk_") under OPENAI_API_KEY by mistake, so we map it for you.
_openai = os.getenv("OPENAI_API_KEY", "")
if not os.getenv("GROQ_API_KEY") and _openai.startswith("gsk_"):
    os.environ["GROQ_API_KEY"] = _openai

# Groq retired these on 2026-08-16; auto-upgrade so an old .env keeps working.
RETIRED_MODELS = {"llama-3.3-70b-versatile", "llama-3.1-8b-instant"}
DEFAULT_MODEL = "openai/gpt-oss-120b"

GROQ_MODEL = os.getenv("GROQ_MODEL", DEFAULT_MODEL).strip()
if GROQ_MODEL.removeprefix("groq/") in RETIRED_MODELS:
    print(f"[config] '{GROQ_MODEL}' was retired by Groq; using {DEFAULT_MODEL}. "
          f"Update GROQ_MODEL in your .env.", file=sys.stderr)
    GROQ_MODEL = DEFAULT_MODEL

LLM_MODEL = os.getenv("LLM_MODEL") or (
    GROQ_MODEL if GROQ_MODEL.startswith("groq/") else f"groq/{GROQ_MODEL}"
)