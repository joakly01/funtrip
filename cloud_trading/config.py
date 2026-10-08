from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    runtime_dir: Path
    password: str
    model_url: str = ""
    model_key: str = ""
    model_name: str = ""
    model_protocol: str = "openai"
    max_turns: int = 24
    secure_cookie: bool = False
    models_path: Path | None = None
    active_model: str = ""
    asr_model_dir: Path | None = None

    @property
    def model_ready(self):
        return bool(self.model_url and self.model_key and self.model_name)

    @classmethod
    def from_env(cls):
        return cls(
            data_dir=Path(os.getenv("TRADING_DATA_DIR", str(ROOT / "data/cloud/trading"))).resolve(),
            runtime_dir=Path(os.getenv("TRADING_RUNTIME_DIR", str(ROOT / "data/cloud/runtime"))).resolve(),
            password=os.getenv("TRADING_PASSWORD", ""),
            model_url=os.getenv("TRADING_MODEL_BASE_URL", ""),
            model_key=os.getenv("TRADING_MODEL_API_KEY", ""),
            model_name=os.getenv("TRADING_MODEL_NAME", ""),
            model_protocol=os.getenv("TRADING_MODEL_PROTOCOL", "openai"),
            max_turns=int(os.getenv("TRADING_MAX_TURNS", "24")),
            secure_cookie=os.getenv("TRADING_SECURE_COOKIE", "0") == "1",
            models_path=Path(os.getenv("TRADING_MODELS_FILE", str(ROOT / "config/models.toml"))).resolve(),
            active_model=os.getenv("TRADING_ACTIVE_MODEL", "kimi"),
            asr_model_dir=Path(os.environ["TRADING_ASR_MODEL_DIR"]).resolve()
                if os.getenv("TRADING_ASR_MODEL_DIR") else None,
        )
