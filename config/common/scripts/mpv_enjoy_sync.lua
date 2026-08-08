local utils = require('mp.utils')

local menu_type = 'mpv_enjoy_sync'
local step = 0.1

local function normalize_delay(value)
    if math.abs(value) < 0.0005 then
        return 0
    end
    return tonumber(string.format('%.3f', value))
end

local function get_delay(property)
    return normalize_delay(mp.get_property_number(property, 0) or 0)
end

local function set_delay(property, value)
    mp.set_property_number(property, normalize_delay(value))
end

local function format_delay(property)
    return string.format('%+.2f 秒', get_delay(property))
end

local function menu_data()
    return {
        type = menu_type,
        title = '音画同步',
        search_style = 'disabled',
        keep_open = true,
        callback = {mp.get_script_name(), 'handle-menu-event'},
        items = {
            {
                title = '字幕延迟',
                hint = format_delay('sub-delay'),
                selectable = false,
                bold = true,
                value = 'ignore',
            },
            {title = '字幕延迟 -0.1 秒', value = 'sub-minus'},
            {title = '字幕延迟 +0.1 秒', value = 'sub-plus'},
            {title = '字幕延迟归零', value = 'sub-reset', separator = true},
            {
                title = '音频延迟',
                hint = format_delay('audio-delay'),
                selectable = false,
                bold = true,
                value = 'ignore',
            },
            {title = '音频延迟 -0.1 秒', value = 'audio-minus'},
            {title = '音频延迟 +0.1 秒', value = 'audio-plus'},
            {title = '音频延迟归零', value = 'audio-reset', separator = true},
            {title = '字幕和音频延迟全部归零', value = 'reset-all'},
        },
    }
end

local function send_menu(command)
    mp.commandv(
        'script-message-to',
        'uosc',
        command,
        utils.format_json(menu_data())
    )
end

local function open_menu()
    send_menu('open-menu')
end

local actions = {
    ['sub-minus'] = {'sub-delay', -step},
    ['sub-plus'] = {'sub-delay', step},
    ['sub-reset'] = {'sub-delay', 0, true},
    ['audio-minus'] = {'audio-delay', -step},
    ['audio-plus'] = {'audio-delay', step},
    ['audio-reset'] = {'audio-delay', 0, true},
}

local function handle_menu_event(json)
    local event = utils.parse_json(json)
    if type(event) ~= 'table' then
        return
    end
    if event.type == 'back' then
        mp.commandv('script-message-to', 'uosc', 'close-menu', menu_type)
        return
    end
    if event.type ~= 'activate' then
        return
    end

    if event.value == 'reset-all' then
        set_delay('sub-delay', 0)
        set_delay('audio-delay', 0)
    else
        local action = actions[event.value]
        if not action then
            return
        end
        local property, value, is_absolute = action[1], action[2], action[3]
        set_delay(property, is_absolute and value or get_delay(property) + value)
    end
    send_menu('update-menu')
end

mp.register_script_message('open-menu', open_menu)
mp.register_script_message('handle-menu-event', handle_menu_event)
