"""CLI management commands for MCP Todo backend."""

import argparse
import asyncio
import getpass
import subprocess
import sys
from datetime import datetime

from pathlib import Path

from .core.config import settings
from .core.database import connect, close_db
from .core.security import hash_password
from .models.user import AuthType, User


async def create_admin_user(email: str, password: str, name: str) -> None:
    """Create an admin user. Assumes DB is already connected."""
    existing = await User.find_one(User.email == email)
    if existing:
        print(f"User already exists: {email} (admin={existing.is_admin})")
        return

    user = User(
        email=email,
        name=name,
        auth_type=AuthType.admin,
        password_hash=hash_password(password),
        is_admin=True,
        is_active=True,
    )
    await user.insert()
    print(f"Admin user created: {email}")


async def _init_admin(email: str, password: str, name: str) -> None:
    """Create an admin user with DB lifecycle management."""
    await connect()
    try:
        await create_admin_user(email, password, name)
    finally:
        await close_db()


async def _backup(output_path: str) -> None:
    """Export database using mongodump."""
    args = [
        "mongodump",
        f"--uri={settings.MONGO_URI}",
        f"--db={settings.MONGO_DBNAME}",
        "--gzip",
        f"--archive={output_path}",
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"Error: mongodump failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Backup saved to {output_path}")


async def _restore(input_path: str) -> None:
    """Restore database using mongorestore."""
    args = [
        "mongorestore",
        f"--uri={settings.MONGO_URI}",
        f"--db={settings.MONGO_DBNAME}",
        "--gzip",
        f"--archive={input_path}",
        "--drop",
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"Error: mongorestore failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("Restore completed")


async def _import_docsite(
    name: str,
    docs_dir_str: str,
    source_url: str,
    description: str,
) -> None:
    """Import a documentation site from a local directory."""
    await connect()
    try:
        docs_dir = Path(docs_dir_str).resolve()
        if not docs_dir.is_dir():
            print(f"Error: directory not found: {docs_dir}", file=sys.stderr)
            sys.exit(1)

        assets_dir = Path(settings.DOCSITE_ASSETS_DIR)
        assets_dir.mkdir(parents=True, exist_ok=True)

        # Initialize search index if available
        from .services.docsite_search import TANTIVY_AVAILABLE
        if TANTIVY_AVAILABLE:
            from .services.docsite_search import (
                DocSiteSearchIndex,
                DocSiteSearchIndexer,
                DocSiteSearchService,
            )
            index = DocSiteSearchIndex(Path(settings.DOCSITE_INDEX_DIR))
            indexer = DocSiteSearchIndexer(index)
            DocSiteSearchIndexer.set_instance(indexer)
            DocSiteSearchService.set_instance(DocSiteSearchService(index))

        from .services.docsite_import import import_docsite
        site = await import_docsite(
            name=name,
            docs_dir=docs_dir,
            assets_dir=assets_dir,
            source_url=source_url,
            description=description,
        )
        print(f"DocSite imported: {site.name} (id={site.id}, pages={site.page_count})")
    finally:
        await close_db()


def _resolve_value(args_val: str | None, env_val: str, prompt_msg: str, *, secret: bool = False) -> str:
    """Resolve value from: CLI arg > env var > interactive prompt."""
    if args_val:
        return args_val
    if env_val:
        return env_val
    if not sys.stdin.isatty():
        print(f"Error: {prompt_msg} is required (use argument or env var)", file=sys.stderr)
        sys.exit(1)
    if secret:
        return getpass.getpass(f"{prompt_msg}: ")
    return input(f"{prompt_msg}: ")


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Todo management CLI")
    sub = parser.add_subparsers(dest="command")

    init_cmd = sub.add_parser("init-admin", help="Create initial admin user")
    init_cmd.add_argument("--email", help="Admin email (or INIT_ADMIN_EMAIL env)")
    init_cmd.add_argument("--password", help="Admin password (or INIT_ADMIN_PASSWORD env)")
    init_cmd.add_argument("--name", default="Admin", help="Display name (default: Admin)")

    backup_cmd = sub.add_parser("backup", help="Export database using mongodump")
    backup_cmd.add_argument("--output", "-o", help="Output file path")

    restore_cmd = sub.add_parser("restore", help="Restore database using mongorestore")
    restore_cmd.add_argument("input", help="Backup file path (.agz)")
    restore_cmd.add_argument(
        "--confirm", action="store_true", required=True,
        help="Confirm data replacement",
    )

    import_ds_cmd = sub.add_parser("import-docsite", help="Import a documentation site from a local directory")
    import_ds_cmd.add_argument("docs_dir", help="Path to docs directory (e.g. ./tmp/PICO/docs_ja)")
    import_ds_cmd.add_argument("--name", required=True, help="Display name for the doc site")
    import_ds_cmd.add_argument("--source-url", default="", help="Original source URL")
    import_ds_cmd.add_argument("--description", default="", help="Site description")

    args = parser.parse_args()

    if args.command == "init-admin":
        email = _resolve_value(args.email, settings.INIT_ADMIN_EMAIL, "Admin email")
        password = _resolve_value(args.password, settings.INIT_ADMIN_PASSWORD, "Admin password", secret=True)

        if len(password) < 6:
            print("Error: password must be at least 6 characters", file=sys.stderr)
            sys.exit(1)

        asyncio.run(_init_admin(email, password, args.name))

    elif args.command == "backup":
        output = args.output or f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.agz"
        asyncio.run(_backup(output))

    elif args.command == "restore":
        asyncio.run(_restore(args.input))

    elif args.command == "import-docsite":
        asyncio.run(_import_docsite(
            name=args.name,
            docs_dir_str=args.docs_dir,
            source_url=args.source_url,
            description=args.description,
        ))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
