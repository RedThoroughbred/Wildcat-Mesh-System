#!/usr/bin/env python3
"""
Bulk update command_handlers.py to use messages.json
This script helps migrate hardcoded strings to use the MESSAGES dictionary
"""

# Note: This file has been updated for the most critical functions.
# The remaining functions still work with hardcoded strings as fallbacks.
# You can edit messages.json and those changes will take effect for
# the functions that have been migrated.

# Key functions migrated:
# - build_menu (all menu labels)
# - handle_help_command (menu headers)
# - handle_mail_command
# - handle_bulletin_command
# - handle_exit_command

print("Messages system partially migrated.")
print("Edit messages.json to customize BBS text.")
print("Remaining functions will be migrated in future updates.")
