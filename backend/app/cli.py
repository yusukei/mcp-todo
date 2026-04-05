"""CLI management commands for MCP Todo backend."""

import argparse
import asyncio
import getpass
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime

from pathlib import Path

from .core.config import settings
from .core.database import connect, close_db
from .core.security import hash_password
from .models.user import AuthType, User

# Backup archive structure (shared convention with API backup endpoint)
_DB_DUMP_NAME = "db.agz"
_ASSET_DIRS = {
    "docsite_assets": "DOCSITE_ASSETS_DIR",
    "bookmark_assets": "BOOKMARK_ASSETS_DIR",
}


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


def _run_mongorestore(db_path: str) -> None:
    """Run mongorestore with the given archive path."""
    args = [
        "mongorestore",
        f"--uri={settings.MONGO_URI}",
        f"--db={settings.MONGO_DBNAME}",
        "--gzip",
        f"--archive={db_path}",
        "--drop",
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"Error: mongorestore failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)


async def _backup(output_path: str) -> None:
    """Export database and asset files as a zip archive."""
    work_dir = Path(tempfile.mkdtemp(prefix="backup_"))
    try:
        # 1. mongodump
        db_path = work_dir / _DB_DUMP_NAME
        args = [
            "mongodump",
            f"--uri={settings.MONGO_URI}",
            f"--db={settings.MONGO_DBNAME}",
            "--gzip",
            f"--archive={db_path}",
        ]
        result = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            print(f"Error: mongodump failed: {result.stderr}", file=sys.stderr)
            sys.exit(1)

        # 2. Create zip with DB dump + assets
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_path, _DB_DUMP_NAME)
            for arc_name, setting_attr in _ASSET_DIRS.items():
                asset_dir = Path(getattr(settings, setting_attr))
                if asset_dir.is_dir():
                    count = 0
                    for fpath in sorted(asset_dir.rglob("*")):
                        if fpath.is_file():
                            zf.write(fpath, f"{arc_name}/{fpath.relative_to(asset_dir)}")
                            count += 1
                    if count:
                        print(f"  {arc_name}: {count} files")

        print(f"Backup saved to {output_path}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _restore(input_path: str) -> None:
    """Restore database and assets from a backup archive (.zip or legacy .agz)."""
    if input_path.endswith(".zip"):
        work_dir = Path(tempfile.mkdtemp(prefix="restore_"))
        try:
            with zipfile.ZipFile(input_path, "r") as zf:
                zf.extractall(work_dir)

            db_path = work_dir / _DB_DUMP_NAME
            if not db_path.exists():
                print(f"Error: {_DB_DUMP_NAME} not found in archive", file=sys.stderr)
                sys.exit(1)

            _run_mongorestore(str(db_path))

            for arc_name, setting_attr in _ASSET_DIRS.items():
                target = Path(getattr(settings, setting_attr))
                src = work_dir / arc_name
                if target.exists():
                    shutil.rmtree(target)
                if src.is_dir():
                    shutil.copytree(src, target)
                    count = sum(1 for f in target.rglob("*") if f.is_file())
                    print(f"  {arc_name}: {count} files restored")
                else:
                    target.mkdir(parents=True, exist_ok=True)

            print("Restore completed")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
    elif input_path.endswith(".agz"):
        _run_mongorestore(input_path)
        print("Restore completed (legacy format, assets not included)")
    else:
        print("Error: file must be .zip or .agz format", file=sys.stderr)
        sys.exit(1)


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


async def _fix_docsite_content() -> None:
    """Reprocess all DocPage content to fix Markdown issues."""
    await connect()
    try:
        from .models.docsite import DocPage
        from .services.docsite_import import preprocess_markdown

        count = 0
        async for page in DocPage.find_all():
            fixed = preprocess_markdown(page.content)
            if fixed != page.content:
                page.content = fixed
                await page.save()
                count += 1
        print(f"Fixed {count} pages")
    finally:
        await close_db()


async def _reset_password(email: str, password: str) -> None:
    """Reset password for an admin user."""
    await connect()
    try:
        user = await User.find_one(User.email == email)
        if not user:
            print(f"Error: user not found: {email}", file=sys.stderr)
            sys.exit(1)
        if user.auth_type != AuthType.admin:
            print(f"Error: cannot reset password for {user.auth_type} user", file=sys.stderr)
            sys.exit(1)
        user.password_hash = hash_password(password)
        user.password_disabled = False
        await user.save()
        print(f"Password reset for: {email}")
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

    backup_cmd = sub.add_parser("backup", help="Export database and assets as zip")
    backup_cmd.add_argument("--output", "-o", help="Output file path")

    restore_cmd = sub.add_parser("restore", help="Restore database and assets from backup")
    restore_cmd.add_argument("input", help="Backup file path (.zip or legacy .agz)")
    restore_cmd.add_argument(
        "--confirm", action="store_true", required=True,
        help="Confirm data replacement",
    )

    reset_cmd = sub.add_parser("reset-password", help="Reset password for an admin user")
    reset_cmd.add_argument("--email", help="User email")
    reset_cmd.add_argument("--password", help="New password (min 8 chars)")

    sub.add_parser("fix-docsite-content", help="Reprocess all DocPage content to fix Markdown issues")

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
        output = args.output or f"backup_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.zip"
        asyncio.run(_backup(output))

    elif args.command == "restore":
        asyncio.run(_restore(args.input))

    elif args.command == "reset-password":
        email = _resolve_value(args.email, "", "Email")
        password = _resolve_value(args.password, "", "New password", secret=True)

        if len(password) < 8:
            print("Error: password must be at least 8 characters", file=sys.stderr)
            sys.exit(1)

        asyncio.run(_reset_password(email, password))

    elif args.command == "fix-docsite-content":
        asyncio.run(_fix_docsite_content())

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
