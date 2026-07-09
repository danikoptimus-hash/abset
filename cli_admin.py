"""CLI abkit-admin: user management for server mode (ABKIT_MODE=db, requires
DATABASE_URL) — DOCKER.md §4.3. Like `superset fab create-admin`: commands
don't require a logged-in user — a trusted operation (run inside the
container, `docker compose exec backend abkit-admin ...`)."""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.table import Table

from abkit import PRODUCT_NAME
from abkit.auth.guards import AuthError
from abkit.auth.service import admin_create_user, admin_reset_password
from abkit.db.repositories import UserRepo

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, help=f"{PRODUCT_NAME} admin CLI — user management (ABKIT_MODE=db)")
# width=120: list-users has 7 columns (email/first/last/role/active/created/
# last login) — the real terminal width auto-detect (or the 80-col default in
# non-TTY contexts like CliRunner) squeezes them enough to truncate emails.
console = Console(legacy_windows=False, width=120)

_ROLES = ("viewer", "editor", "admin")


@app.command("create-admin")
def create_admin(
    email: str = typer.Option(..., "--email"),
    first_name: str = typer.Option("Admin", "--first-name"),
    last_name: str = typer.Option("", "--last-name"),
    password: str = typer.Option(None, "--password"),
) -> None:
    """Create the first administrator (bootstrap for fully automated deployment)."""
    try:
        user_id, generated = admin_create_user(
            None, email=email, first_name=first_name, last_name=last_name, role="admin", password=password
        )
    except AuthError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]Administrator created:[/green] {email} (id={user_id})")
    if password is None:
        console.print(f"Temporary password (save it — shown only once): [bold]{generated}[/bold]")


@app.command("create-user")
def create_user(
    email: str = typer.Option(..., "--email"),
    role: str = typer.Option(..., "--role"),
    first_name: str = typer.Option("", "--first-name"),
    last_name: str = typer.Option("", "--last-name"),
    password: str = typer.Option(None, "--password"),
) -> None:
    if role not in _ROLES:
        console.print(f"[red]Error:[/red] unknown role '{role}'. Allowed: {', '.join(_ROLES)}")
        raise typer.Exit(code=1)
    try:
        user_id, generated = admin_create_user(
            None, email=email, first_name=first_name or email, last_name=last_name, role=role,
            password=password,
        )
    except AuthError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]User created:[/green] {email} (role={role}, id={user_id})")
    if password is None:
        console.print(f"Temporary password: [bold]{generated}[/bold]")


@app.command("reset-password")
def reset_password(
    email: str = typer.Option(..., "--email"),
    password: str = typer.Option(None, "--password"),
) -> None:
    try:
        generated = admin_reset_password(None, target_email=email, new_password=password)
    except AuthError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)
    console.print(f"[green]Password reset for[/green] {email}")
    if password is None:
        console.print(f"Temporary password: [bold]{generated}[/bold]")


@app.command("list-users")
def list_users() -> None:
    users = UserRepo().list_all()
    table = Table(title=f"{PRODUCT_NAME} users")
    table.add_column("Email")
    table.add_column("First name")
    table.add_column("Last name")
    table.add_column("Role")
    table.add_column("Active")
    table.add_column("Created")
    table.add_column("Last login")
    for u in users:
        table.add_row(
            u.email,
            u.first_name,
            u.last_name,
            u.role,
            "yes" if u.is_active else "no",
            u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "-",
            u.last_login_at.strftime("%Y-%m-%d %H:%M") if u.last_login_at else "-",
        )
    console.print(table)


@app.command("import-legacy")
def import_legacy(
    dir: str = typer.Option(..., "--dir", help="Old file-mode registry folder (registry.json + experiments)"),
    owner: str = typer.Option(..., "--owner", help="Email of an existing user — owner of the imported experiments"),
) -> None:
    """Import the file-mode (legacy) experiment registry into server mode —
    DOCKER.md §9. Idempotent: re-running does not duplicate experiments
    already imported (matched by name)."""
    from pathlib import Path

    from abkit.db.import_legacy import LegacyImportError, import_legacy_dir

    try:
        result = import_legacy_dir(Path(dir), owner)
    except LegacyImportError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1)

    if result.imported:
        console.print(f"[green]Imported ({len(result.imported)}):[/green] {', '.join(result.imported)}")
    if result.skipped_existing:
        console.print(
            f"[yellow]Already imported, skipped ({len(result.skipped_existing)}):[/yellow] "
            f"{', '.join(result.skipped_existing)}"
        )
    if result.failed:
        console.print(f"[red]Errors ({len(result.failed)}):[/red]")
        for name, err in result.failed.items():
            console.print(f"  {name}: {err}")
    if not result.imported and not result.skipped_existing and not result.failed:
        console.print("No experiments found to import (is registry.json empty?).")


if __name__ == "__main__":
    app()
