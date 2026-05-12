"""
Registry for bot handlers to keep main.py clean and organized.
"""
import logging
from telegram.ext import Application

logger = logging.getLogger(__name__)

def register_all_handlers(app: Application):
    """
    Registers all handlers from various modules in the correct order.
    Order is crucial:
    1. High-priority global handlers (e.g., maintenance guard)
    2. Conversation handlers (they must catch specific inputs first)
    3. Specific CallbackQuery/Message handlers
    4. Global fallback handlers
    """
    # 0. High priority (Group -1) - Already handled in main.py but can be moved here
    # from handlers.general import maintenance_mode_guard
    # app.add_handler(TypeHandler(Update, maintenance_mode_guard), group=-1)

    # 1. Base Commands
    from handlers.general import start, help_info, our_bots
    from handlers.admin import test_notify
    from telegram.ext import CommandHandler, MessageHandler, filters
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_info))
    app.add_handler(CommandHandler("our_bots", our_bots))
    app.add_handler(CommandHandler("test_notify", test_notify))
    
    # Persistent Menu
    app.add_handler(MessageHandler(filters.Regex("^🏠 القائمة الرئيسية$"), start))
    app.add_handler(MessageHandler(filters.Regex("^🤖 بوتاتنا$"), our_bots))

    # 2. Conversations (Highest precedence for text inputs)
    from handlers.fatwa import add_fatwa_conv, edit_conv
    from handlers.search import search_conv
    from handlers.admin import (
        admin_conv, scholar_conv, podcast_conv, category_conv, topic_conv, source_conv, settings_conv
    )
    
    app.add_handler(add_fatwa_conv)
    app.add_handler(edit_conv)
    app.add_handler(search_conv)
    app.add_handler(admin_conv)
    app.add_handler(scholar_conv)
    app.add_handler(podcast_conv)
    app.add_handler(category_conv)
    app.add_handler(topic_conv)
    app.add_handler(source_conv)
    app.add_handler(settings_conv) # Now properly registered as a conversation

    # 3. CallbackQuery Handlers (General/Navigation)
    from telegram.ext import CallbackQueryHandler
    from handlers.general import (
        start_refresh, back_to_main, cancel_operation, how_to_add_bot, show_add_bot_tutorial, noop
    )
    
    app.add_handler(CallbackQueryHandler(start_refresh, pattern='^start_refresh$'))
    app.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_main$'))
    app.add_handler(CallbackQueryHandler(cancel_operation, pattern='^cancel$'))
    app.add_handler(CallbackQueryHandler(help_info, pattern='^help_info$'))
    app.add_handler(CallbackQueryHandler(our_bots, pattern='^our_bots$'))
    app.add_handler(CallbackQueryHandler(how_to_add_bot, pattern='^how_to_add_bot$'))
    app.add_handler(CallbackQueryHandler(show_add_bot_tutorial, pattern='^show_add_bot_tutorial$'))
    app.add_handler(CallbackQueryHandler(noop, pattern='^noop$'))

    # 4. Search Results Pagination & Logic
    from handlers.search import handle_search_pagination, search_latest, search_popular, show_scholar_fatwas_by_id
    app.add_handler(CallbackQueryHandler(handle_search_pagination, pattern='^res_page_'))
    app.add_handler(CallbackQueryHandler(search_latest, pattern='^search_latest$'))
    app.add_handler(CallbackQueryHandler(search_popular, pattern='^search_popular$'))
    app.add_handler(CallbackQueryHandler(show_scholar_fatwas_by_id, pattern='^scholar_fatwas_'))

    # 5. Fatwa Actions (View, Publish, Delete, Broadcast)
    from handlers.fatwa import (
        publish_fatwa, delete_fatwa_confirm, delete_fatwa_final, delete_fatwa_from_all,
        view_fatwa, show_related_fatwas, show_random_fatwa, continue_reading_fatwa, copy_fatwa_full,
        broadcast_fatwa
    )
    
    app.add_handler(CallbackQueryHandler(publish_fatwa, pattern=r'^publish_\d+'))
    app.add_handler(CallbackQueryHandler(delete_fatwa_confirm, pattern=r'^confirm_delete_\d+'))
    app.add_handler(CallbackQueryHandler(delete_fatwa_final, pattern=r'^delete_final_\d+'))
    app.add_handler(CallbackQueryHandler(delete_fatwa_from_all, pattern=r'^del_all_fatwa_\d+$'))
    app.add_handler(CallbackQueryHandler(copy_fatwa_full, pattern=r'^copy_full_\d+'))
    app.add_handler(CallbackQueryHandler(broadcast_fatwa, pattern=r'^broadcast_\d+'))
    app.add_handler(CallbackQueryHandler(show_random_fatwa, pattern=r'^random_fatwa(?:_\d+)?$'))
    app.add_handler(CallbackQueryHandler(continue_reading_fatwa, pattern=r'^continue_read_\d+(?:_.+)?$'))
    app.add_handler(CallbackQueryHandler(view_fatwa, pattern=r'^view_\d+'))
    app.add_handler(CallbackQueryHandler(show_related_fatwas, pattern=r'^related_fatwas_\d+'))

    # 6. Admin Panel Callbacks
    from handlers.admin import (
        admin_panel, settings_panel, start_set_weekly_day, apply_weekly_day, show_admin_drafts,
        show_duplicates, manage_admins, list_admins_handler, 
        manage_scholars_panel, show_scholars_admin, view_scholar_admin, 
        manage_subscribers, cleanup_inactive_subscribers, cancel_podcast_broadcast,
        manage_categories, manage_sources, manage_source, confirm_delete_source, 
        delete_source_handler, confirm_delete_category_handler, delete_category_handler,
        handle_category_type_filter, start_add_category_admin, view_topics_handler,
        show_statistics, backup_database_handler, toggle_maintenance_mode, manage_links_panel,
        show_missing_links
    )
    # Note: Some functions might need adjustment if they are not explicitly imported/exported
    
    app.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin_panel$'))
    app.add_handler(CallbackQueryHandler(settings_panel, pattern='^admin_settings$'))
    app.add_handler(CallbackQueryHandler(start_set_weekly_day, pattern='^set_weekly_day$'))
    app.add_handler(CallbackQueryHandler(apply_weekly_day, pattern='^weekly_day_'))
    app.add_handler(CallbackQueryHandler(show_admin_drafts, pattern='^admin_drafts'))
    app.add_handler(CallbackQueryHandler(show_duplicates, pattern='^admin_duplicates'))
    app.add_handler(CallbackQueryHandler(manage_admins, pattern='^manage_admins$'))
    app.add_handler(CallbackQueryHandler(manage_scholars_panel, pattern='^manage_scholars$'))
    app.add_handler(CallbackQueryHandler(show_scholars_admin, pattern='^scholars_list'))
    app.add_handler(CallbackQueryHandler(show_scholars_admin, pattern='^clear_admin_schol_search$'))
    app.add_handler(CallbackQueryHandler(view_scholar_admin, pattern='^scholar_view_'))
    app.add_handler(CallbackQueryHandler(manage_subscribers, pattern='^manage_subscribers'))
    app.add_handler(CallbackQueryHandler(cleanup_inactive_subscribers, pattern='^cleanup_subscribers$'))
    app.add_handler(CallbackQueryHandler(cancel_podcast_broadcast, pattern='^podcast_cancel_'))
    app.add_handler(CallbackQueryHandler(list_admins_handler, pattern='^list_admins$'))
    app.add_handler(CallbackQueryHandler(manage_categories, pattern='^manage_categories'))
    app.add_handler(CallbackQueryHandler(manage_sources, pattern='^manage_sources'))
    app.add_handler(CallbackQueryHandler(manage_source, pattern='^manage_source_'))
    app.add_handler(CallbackQueryHandler(confirm_delete_source, pattern='^confirm_delete_source_'))
    app.add_handler(CallbackQueryHandler(delete_source_handler, pattern='^delete_source_'))
    app.add_handler(CallbackQueryHandler(confirm_delete_category_handler, pattern='^confirm_delete_category_'))
    app.add_handler(CallbackQueryHandler(delete_category_handler, pattern='^delete_category_'))
    app.add_handler(CallbackQueryHandler(handle_category_type_filter, pattern='^admin_cat_type_'))
    app.add_handler(CallbackQueryHandler(start_add_category_admin, pattern='^add_cat_(fiqh|topic)$'))
    app.add_handler(CallbackQueryHandler(view_topics_handler, pattern='^view_topics'))
    app.add_handler(CallbackQueryHandler(show_statistics, pattern='^stats$'))
    app.add_handler(CallbackQueryHandler(backup_database_handler, pattern='^backup_db$'))
    app.add_handler(CallbackQueryHandler(toggle_maintenance_mode, pattern='^toggle_maintenance_mode$'))
    app.add_handler(CallbackQueryHandler(manage_links_panel, pattern='^manage_links$'))
    app.add_handler(CallbackQueryHandler(show_missing_links, pattern='^missing_links_'))

    # 7. Channel Management
    from handlers.channels import (
        track_chat_member, manage_channels_panel, show_channel_status, list_channels_handler,
        auto_publish_panel, toggle_auto_publish, force_publish_handler, targeted_publish_panel,
        toggle_targeted_publish, start_select_publish_category, start_search_publish_category,
        clear_publish_category_search, handle_publish_category_search_input, set_publish_category,
        start_select_publish_topics, toggle_publish_topic, clear_publish_topics_selection,
        cleanup_inactive, start_schedule_fatwa_once, clear_scheduled_fatwa_handler
    )
    from telegram.ext import ChatMemberHandler
    
    app.add_handler(ChatMemberHandler(track_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(manage_channels_panel, pattern='^manage_channels$'))
    app.add_handler(CallbackQueryHandler(toggle_auto_publish, pattern='^toggle_auto_publish$'))
    app.add_handler(CallbackQueryHandler(show_channel_status, pattern='^status_(channels|groups)'))
    app.add_handler(CallbackQueryHandler(list_channels_handler, pattern='^list_'))
    app.add_handler(CallbackQueryHandler(cleanup_inactive, pattern='^cleanup_(channel|group)$'))
    
    # Auto Publish
    app.add_handler(CallbackQueryHandler(auto_publish_panel, pattern='^auto_publish_panel$'))
    app.add_handler(CallbackQueryHandler(force_publish_handler, pattern='^force_publish_now$'))
    app.add_handler(CallbackQueryHandler(start_schedule_fatwa_once, pattern='^schedule_fatwa_once$'))
    app.add_handler(CallbackQueryHandler(clear_scheduled_fatwa_handler, pattern='^clear_scheduled_fatwa$'))
    app.add_handler(CallbackQueryHandler(toggle_auto_publish, pattern='^toggle_auto_publish_master$'))
    app.add_handler(CallbackQueryHandler(targeted_publish_panel, pattern='^targeted_publish_panel$'))
    app.add_handler(CallbackQueryHandler(toggle_targeted_publish, pattern='^toggle_targeted_publish$'))
    app.add_handler(CallbackQueryHandler(start_select_publish_category, pattern='^sel_pub_cat_start$'))
    app.add_handler(CallbackQueryHandler(start_select_publish_category, pattern='^sel_pub_cat_page_'))
    app.add_handler(CallbackQueryHandler(start_search_publish_category, pattern='^search_pub_cat$'))
    app.add_handler(CallbackQueryHandler(clear_publish_category_search, pattern='^clear_pub_cat_search$'))
    app.add_handler(CallbackQueryHandler(set_publish_category, pattern='^set_pub_cat_'))
    app.add_handler(CallbackQueryHandler(start_select_publish_topics, pattern='^sel_pub_top_start$'))
    app.add_handler(CallbackQueryHandler(start_select_publish_topics, pattern='^sel_pub_top_page_'))
    app.add_handler(CallbackQueryHandler(toggle_publish_topic, pattern='^toggle_pub_top_'))
    app.add_handler(CallbackQueryHandler(clear_publish_topics_selection, pattern='^clear_pub_topics$'))

    # 8. Favorites
    from handlers.favorites import toggle_favorite_handler, my_favorites_handler, top_favorites_handler
    app.add_handler(CallbackQueryHandler(toggle_favorite_handler, pattern='^toggle_fav_'))
    app.add_handler(CallbackQueryHandler(my_favorites_handler, pattern='^my_favorites$'))
    app.add_handler(CallbackQueryHandler(my_favorites_handler, pattern='^fav_page_'))
    app.add_handler(CallbackQueryHandler(my_favorites_handler, pattern='^fav_sort_'))
    app.add_handler(CallbackQueryHandler(top_favorites_handler, pattern='^top_favorites$'))

    # 9. User Send Fatwa
    from handlers.user_publish import (
        user_send_fatwa_panel, toggle_user_channel_select, toggle_select_all,
        user_send_fatwa_execute, user_send_fatwa_cancel, user_send_fatwa_send_valid
    )
    app.add_handler(CallbackQueryHandler(user_send_fatwa_panel, pattern='^user_send_fatwa$'))
    app.add_handler(CallbackQueryHandler(user_send_fatwa_panel, pattern='^user_sf_page_'))
    app.add_handler(CallbackQueryHandler(toggle_user_channel_select, pattern='^user_sf_toggle_'))
    app.add_handler(CallbackQueryHandler(toggle_select_all, pattern='^user_sf_selall$'))
    app.add_handler(CallbackQueryHandler(user_send_fatwa_execute, pattern='^user_sf_send$'))
    app.add_handler(CallbackQueryHandler(user_send_fatwa_send_valid, pattern='^user_sf_send_valid$'))
    app.add_handler(CallbackQueryHandler(user_send_fatwa_cancel, pattern='^user_sf_cancel$'))

    # 10. Low Priority / Global Text Catchers (MUST BE LAST)
    # Catch search input for targeted publish
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND) & filters.ChatType.PRIVATE, handle_publish_category_search_input))

    # Error Handler
    from handlers.general import error_handler
    app.add_error_handler(error_handler)

    logger.info("All handlers registered successfully.")
