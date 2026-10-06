import argparse
import asyncio
import getpass
import os
import sys

from rag.auth.service import bootstrap_admin
from rag.db.session import get_sessionmaker
from rag.worker.queue import enqueue


async def _admin_create(email: str, tenant: str) -> None:
    # Never accept the password as an argv flag (visible in shell history and `ps`).
    password = os.environ.get("RAG_ADMIN_PASSWORD") or getpass.getpass("Admin password (min 12 chars): ")
    async with get_sessionmaker()() as db:
        tenant_id = await bootstrap_admin(db, email=email, password=password, tenant_name=tenant)
    print(f"Admin {email} ready in tenant '{tenant}' ({tenant_id})")


async def _reindex(version: int) -> None:
    async with get_sessionmaker()() as db:
        job = enqueue(db, "reindex", {"target_version": version})
        await db.commit()
    print(f"Queued reindex into chunks_v{version} (job {job.id})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rag")
    sub = parser.add_subparsers(dest="group", required=True)
    admin = sub.add_parser("admin").add_subparsers(dest="cmd", required=True)
    create = admin.add_parser("create", help="Create the first tenant + admin (no open sign-up)")
    create.add_argument("--email", required=True)
    create.add_argument("--tenant", required=True)
    reindex = sub.add_parser("reindex", help="Re-embed all chunks into a new collection and flip the alias")
    reindex.add_argument("--version", type=int, required=True)
    args = parser.parse_args()

    try:
        if args.group == "admin" and args.cmd == "create":
            asyncio.run(_admin_create(args.email, args.tenant))
        elif args.group == "reindex":
            asyncio.run(_reindex(args.version))
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
