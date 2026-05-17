"""Role-based access control helpers."""
from __future__ import annotations

import os
import discord

GUEST_ROLE      = os.getenv("DISCORD_GUEST_ROLE",     "")
COLLECTOR_ROLE  = os.getenv("DISCORD_COLLECTOR_ROLE", "")
ADMIN_ROLE      = os.getenv("DISCORD_ADMIN_ROLE",     "")


def _member_has_any_role(member: discord.Member, *role_settings: str) -> bool:
    """True if the member holds at least one of the non-empty configured roles."""
    configured = [r for r in role_settings if r]
    if not configured:
        return True  # nothing configured → unrestricted
    member_role_ids   = {str(r.id) for r in member.roles}
    member_role_names = {r.name    for r in member.roles}
    return any(r in member_role_ids or r in member_role_names for r in configured)


async def _deny(interaction: discord.Interaction, required_role: str) -> None:
    msg = (
        f"You need the **{required_role}** role to use this command."
        if required_role
        else "You do not have permission to use this command."
    )
    await interaction.response.send_message(msg, ephemeral=True)


async def _require_role(
    interaction: discord.Interaction,
    gate_role: str,
    accepted_roles: list[str],
) -> bool:
    """Return True if the user passes the role gate. Sends a denial and returns False otherwise."""
    if not gate_role:
        return True
    if not isinstance(interaction.user, discord.Member):
        return True
    if _member_has_any_role(interaction.user, *accepted_roles):
        return True
    await _deny(interaction, gate_role)
    return False


async def require_guest(interaction: discord.Interaction) -> bool:
    """Read-only commands. Open to all when DISCORD_GUEST_ROLE is not configured."""
    return await _require_role(interaction, GUEST_ROLE, [GUEST_ROLE, COLLECTOR_ROLE, ADMIN_ROLE])


async def require_collector(interaction: discord.Interaction) -> bool:
    """Add/modify commands. Open to all when DISCORD_COLLECTOR_ROLE is not configured."""
    return await _require_role(interaction, COLLECTOR_ROLE, [COLLECTOR_ROLE, ADMIN_ROLE])


async def require_admin(interaction: discord.Interaction) -> bool:
    """Destructive/admin commands. Open to all when DISCORD_ADMIN_ROLE is not configured."""
    return await _require_role(interaction, ADMIN_ROLE, [ADMIN_ROLE])
