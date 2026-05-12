"""
Channels Management Package (handlers/channels)
---------------------------------------------
Handles channel/group management, auto-publishing, and background jobs.
"""

from .tracking import track_chat_member
from .panel import (
    manage_channels_panel,
    show_channel_status,
    list_channels_handler,
    cleanup_inactive
)
from .autopublish import (
    auto_publish_panel,
    toggle_auto_publish,
    targeted_publish_panel,
    toggle_targeted_publish,
    start_select_publish_category,
    start_search_publish_category,
    clear_publish_category_search,
    handle_publish_category_search_input,
    set_publish_category,
    start_select_publish_topics,
    toggle_publish_topic,
    clear_publish_topics_selection,
    start_schedule_fatwa_once
)
from .publish import (
    force_publish_handler
)
from .jobs import (
    daily_fatwa_job,
    weekly_fatwa_report_job
)

__all__ = [
    'track_chat_member',
    'manage_channels_panel',
    'show_channel_status',
    'list_channels_handler',
    'cleanup_inactive',
    'auto_publish_panel',
    'toggle_auto_publish',
    'targeted_publish_panel',
    'toggle_targeted_publish',
    'start_select_publish_category',
    'start_search_publish_category',
    'clear_publish_category_search',
    'handle_publish_category_search_input',
    'set_publish_category',
    'start_select_publish_topics',
    'toggle_publish_topic',
    'clear_publish_topics_selection',
    'force_publish_handler',
    'start_schedule_fatwa_once',
    'daily_fatwa_job',
    'weekly_fatwa_report_job'
]
