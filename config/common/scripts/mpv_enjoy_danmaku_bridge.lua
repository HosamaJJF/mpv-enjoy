local utils = require('mp.utils')

local switch_property = 'user-data/uosc_danmaku/danmaku-switch-on'

local buttons = {
    danmaku = {
        icon = 'search',
        tooltip = '弹幕搜索',
        command = 'script-message open_search_danmaku_menu',
    },
    danmaku_menu = {
        icon = 'grid_view',
        tooltip = '弹幕设置',
        command = 'script-message open_add_total_menu',
    },
}

local function sync_switch(enabled)
    if enabled == nil then
        enabled = mp.get_property_bool(switch_property, false)
    end
    mp.commandv(
        'script-message-to',
        'uosc',
        'set',
        'show_danmaku',
        enabled and 'on' or 'off'
    )
end

local function register_integration()
    for name, definition in pairs(buttons) do
        mp.commandv(
            'script-message-to',
            'uosc',
            'set-button',
            name,
            utils.format_json(definition)
        )
    end
    -- uosc sends this once at startup; repeating it avoids a script-load race.
    mp.commandv('script-message', 'uosc-version', '5.13.0')
    sync_switch()
end

mp.observe_property(switch_property, 'bool', function(_, enabled)
    if enabled ~= nil then
        sync_switch(enabled)
    end
end)
mp.add_timeout(0.75, register_integration)
mp.register_event('file-loaded', register_integration)
