ONBRIEF_CHECK = {
    "name": "onbrief_check",
    "description": (
        "Check a draft social post or outbound message against the user's campaign rules "
        "(required hashtags and mentions, banned words, length, links, duplicates of recent posts) "
        "before sending it. Sends nothing. Returns each failing rule with a fix, and a suggested "
        "text when the fix is mechanical. Guarded posting tools are checked again at send time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The exact post text to check."},
            "campaign": {"type": "string",
                         "description": "Optional campaign name; omit to use the active or matching campaigns."},
        },
        "required": ["text"],
    },
}
