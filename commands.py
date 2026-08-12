"""
Command-menu registration with Telegram scopes.

Called once at startup (via Application.post_init) so the Telegram command
button (/) shows the right list depending on context:

  * Private chats            -> full set, including /setkey and /delkey
  * Group chats             -> everything except /setkey and /delkey
  * Group chat administrators -> same group set (admins can set the group
                                personality via /personality)

We delete existing commands per scope first (clean slate) then set the new ones.
"""
import logging

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
)

logger = logging.getLogger("bot")

PRIVATE_COMMANDS = [
    BotCommand("start", "Welcome & setup"),
    BotCommand("help", "Show all commands & help"),
    BotCommand("setkey", "Set your Ollama API key (DM only)"),
    BotCommand("delkey", "Remove your stored API key"),
    BotCommand("model", "List & pick a free model"),
    BotCommand("personality", "View or set personality"),
    BotCommand("lang", "Set reply language"),
    BotCommand("format", "Set reply formatting"),
    BotCommand("clear", "Clear this chat's memory"),
    BotCommand("stats", "Show model & memory info"),
]

# Groups: never expose key management (keys are personal and DM-only).
GROUP_COMMANDS = [
    BotCommand("start", "Welcome & setup"),
    BotCommand("help", "Show all commands & help"),
    BotCommand("model", "List & pick a free model"),
    BotCommand("personality", "View or set group personality"),
    BotCommand("lang", "Set reply language"),
    BotCommand("format", "Set reply formatting"),
    BotCommand("clear", "Clear this chat's memory"),
    BotCommand("stats", "Show model & memory info"),
]

# Admins get the same set (they can set the group personality via /personality).
ADMIN_COMMANDS = GROUP_COMMANDS


async def register_commands(application) -> None:
    """Register scoped command menus. Safe to call once at bot startup."""
    bot = application.bot
    try:
        # Clean slate for each scope before (re)setting.
        await bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
        await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
        await bot.delete_my_commands(scope=BotCommandScopeAllChatAdministrators())

        await bot.set_my_commands(
            PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats()
        )
        await bot.set_my_commands(
            GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats()
        )
        await bot.set_my_commands(
            ADMIN_COMMANDS, scope=BotCommandScopeAllChatAdministrators()
        )
        logger.info("Registered Telegram command menus (scoped).")
    except Exception as exc:  # network / permission issues shouldn't crash startup
        logger.warning("Command registration failed: %s", type(exc).__name__)
