import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8768
    max_run_usd: float = 10
    max_job_usd: float = 0.25
    max_jobs: int = 40
    allowed_origins: set[str] = field(
        default_factory=lambda: {"http://127.0.0.1:8768", "http://localhost:8768"}
    )

    @classmethod
    def from_env(cls, env_file=None):
        # An explicit path prevents python-dotenv from searching a parent project.
        load_dotenv(Path(env_file) if env_file else Path.cwd() / ".env", override=False)
        port = int(os.getenv("MARKETPLACE_PORT", "8768"))
        origins = os.getenv(
            "MARKETPLACE_ALLOWED_ORIGINS", f"http://127.0.0.1:{port},http://localhost:{port}"
        )
        settings = cls(
            data_dir=Path(os.getenv("MARKETPLACE_DATA_DIR", "data")).resolve(),
            host=os.getenv("MARKETPLACE_HOST", "127.0.0.1"),
            port=port,
            max_run_usd=float(os.getenv("MARKETPLACE_MAX_RUN_USD", "10")),
            max_job_usd=float(os.getenv("MARKETPLACE_MAX_JOB_USD", "0.25")),
            max_jobs=int(os.getenv("MARKETPLACE_MAX_JOBS", "40")),
            allowed_origins={s.strip().rstrip("/") for s in origins.split(",") if s.strip()},
        )
        if (
            not 0 < settings.max_job_usd <= 10
            or not 0 < settings.max_run_usd <= 100
            or not 1 <= settings.max_jobs <= 100
        ):
            raise ValueError("Invalid collection limits in .env")
        return settings
