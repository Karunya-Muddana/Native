from pathlib import Path


RAG_MODEL = "nomic-embed-text"
CODER_MAX_STEPS = 5
CODER_MODEL = "hhao/qwen2.5-coder-tools:7b"
TOOL_MAX_RETRIES = 2


BASE_DIR = Path(__file__).resolve().parents[2]
CHECKPOINT_DB = str(BASE_DIR / "app" / "data" / "agent.db")
Path(CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)