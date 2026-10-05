import os
from dotenv import load_dotenv

load_dotenv()

KODEKEY_API_KEY = os.getenv("KODEKEY_API_KEY")
KODEKEY_BASE_URL = os.getenv(
    "KODEKEY_BASE_URL",
    "https://api.ai.kodekloud.com/v1"
)
KODEKEY_MODEL = os.getenv("KODEKEY_MODEL")

if not KODEKEY_API_KEY:
    raise ValueError("KODEKEY_API_KEY is not set")

if not KODEKEY_MODEL:
    raise ValueError("KODEKEY_MODEL is not set")
