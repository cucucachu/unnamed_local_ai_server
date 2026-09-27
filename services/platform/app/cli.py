"""Recovery / ops CLI - the physical-access path to accounts (docs/PLATFORM.md §4).

Run inside the running container (the image's `python` is the venv's):

    docker compose exec platform python -m app.cli list-users
    docker compose exec platform python -m app.cli create-user alice --role admin
    printf '%s\\n' "$PW" | docker compose exec -T platform \\
        python -m app.cli create-user e2e-bob --password-stdin

Users created here never complete bootstrap (`POST /api/auth/setup` stays
open until someone uses the setup code). Errors print `error: <code>` and
exit 1.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from collections.abc import Awaitable, Callable

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.core import users
from app.core.config import Settings
from app.core.errors import PlatformError


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("New password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("error: passwords_do_not_match")
    return password


def _status(row: dict) -> str:
    return "disabled" if row["disabled_at"] else "active"


async def _create_user(conn: AsyncConnection, args: argparse.Namespace) -> None:
    password = _read_password(args)
    row = await users.create_user(
        conn,
        username=args.username,
        display_name=args.display_name or args.username,
        password=password,
        role=args.role,
    )
    print(f"created {row['role']} {row['username']} (id {row['id']}, uid {row['uid']})")


async def _reset_password(conn: AsyncConnection, args: argparse.Namespace) -> None:
    row = await users.get_user_by_username(conn, args.username)
    await users.set_password(conn, row["id"], _read_password(args), clear_totp=args.clear_totp)
    cleared = " and cleared TOTP" if args.clear_totp else ""
    print(f"reset password{cleared} for {row['username']}; all their sessions were revoked")


async def _set_role(conn: AsyncConnection, args: argparse.Namespace) -> None:
    row = await users.get_user_by_username(conn, args.username)
    row = await users.update_user(conn, row["id"], role=args.role)
    print(f"{row['username']} is now {row['role']}")


async def _set_disabled(conn: AsyncConnection, args: argparse.Namespace, disabled: bool) -> None:
    row = await users.get_user_by_username(conn, args.username)
    row = await users.update_user(conn, row["id"], disabled=disabled)
    print(f"{row['username']} is now {_status(row)}")


async def _list_users(conn: AsyncConnection, args: argparse.Namespace) -> None:
    rows = await users.list_users(conn)
    if args.json:
        print(json.dumps(rows, default=str, indent=2))
        return
    print(f"{'USERNAME':<24} {'ROLE':<7} {'UID':<6} {'STATUS':<9} {'TOTP':<5} ID")
    for row in rows:
        totp = "yes" if row["totp_enabled"] else "no"
        print(
            f"{row['username']:<24} {row['role']:<7} {row['uid']:<6} "
            f"{_status(row):<9} {totp:<5} {row['id']}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def password_opt(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--password-stdin",
            action="store_true",
            help="read the password from the first line of stdin instead of prompting",
        )

    p = sub.add_parser("create-user", help="create a user (does not complete bootstrap)")
    p.add_argument("username")
    p.add_argument("--display-name", help="defaults to the username")
    p.add_argument("--role", choices=users.ROLES, default="member")
    password_opt(p)
    p.set_defaults(run=_create_user)

    p = sub.add_parser("reset-password", help="set a new password and revoke all sessions")
    p.add_argument("username")
    p.add_argument("--clear-totp", action="store_true", help="also remove TOTP (lost device)")
    password_opt(p)
    p.set_defaults(run=_reset_password)

    p = sub.add_parser("set-role", help="make a user admin or member (never the last admin)")
    p.add_argument("username")
    p.add_argument("role", choices=users.ROLES)
    p.set_defaults(run=_set_role)

    p = sub.add_parser("disable-user", help="disable a user and revoke their sessions")
    p.add_argument("username")
    p.set_defaults(run=lambda conn, args: _set_disabled(conn, args, True))

    p = sub.add_parser("enable-user", help="re-enable a disabled user")
    p.add_argument("username")
    p.set_defaults(run=lambda conn, args: _set_disabled(conn, args, False))

    p = sub.add_parser("list-users", help="list every user")
    p.add_argument("--json", action="store_true")
    p.set_defaults(run=_list_users)
    return parser


async def _main(
    run: Callable[[AsyncConnection, argparse.Namespace], Awaitable[None]],
    args: argparse.Namespace,
    dsn: str,
) -> None:
    async with await AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        await run(conn, args)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    dsn = (settings or Settings()).database_dsn
    try:
        asyncio.run(_main(args.run, args, dsn))
    except PlatformError as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
