import argparse
import json
from pathlib import Path

from .settings import Settings
from .store import Store, digest


def main():
    parser = argparse.ArgumentParser(
        description="Search vehicle listings with evidence-based filters."
    )
    parser.add_argument(
        "--env-file", type=Path, help="Load this .env instead of the current directory's .env"
    )
    parser.add_argument("--data-dir", type=Path, help="Override the local database directory")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve", help="Start the local dashboard")
    server.add_argument("--port", type=int)
    server.add_argument("--host")
    sub.add_parser("demo", help="Start a separate sample database with illustrative photos")
    imp = sub.add_parser("import", help="Import an Apify JSON array without a paid collection")
    imp.add_argument("file", type=Path)
    imp.add_argument("--observed-at", help="Original observation time in ISO 8601 with timezone")
    args = parser.parse_args()
    settings = Settings.from_env(args.env_file)
    if args.data_dir:
        settings.data_dir = args.data_dir.resolve()
    if args.command == "demo":
        settings.data_dir = settings.data_dir / "demo"
        from .sample import seed

        seed(Store(settings.data_dir))
    if args.command in {"serve", "demo"}:
        import uvicorn

        from .app import create_app

        settings.port = getattr(args, "port", None) or settings.port
        settings.host = getattr(args, "host", None) or settings.host
        settings.allowed_origins.update(
            {f"http://127.0.0.1:{settings.port}", f"http://localhost:{settings.port}"}
        )
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
    else:
        store = Store(settings.data_dir)
        if args.command == "import":
            records = json.loads(args.file.read_text(encoding="utf-8-sig"))
            print(
                f"Imported {store.import_records(records, 'import:' + digest(records), args.observed_at)} observations."
            )


if __name__ == "__main__":
    main()
