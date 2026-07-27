local mp = require("mp")
local msg = require("mp.msg")

local function require_printable_ascii(label, value)
    if type(value) ~= "string" or #value == 0 then
        error(label .. " decrypted to an empty value")
    end
    for index = 1, #value do
        local byte = value:byte(index)
        if byte < 0x21 or byte > 0x7e then
            error(label .. " did not decrypt to printable ASCII")
        end
    end
end

local function verify()
    local script_root = os.getenv("MPV_ENJOY_DANMAKU_SCRIPT_ROOT")
    if not script_root or script_root == "" then
        error("MPV_ENJOY_DANMAKU_SCRIPT_ROOT is required")
    end
    script_root = script_root:gsub("[/\\]+$", "")

    local AES = dofile(script_root .. "/modules/aes.lua")
    local Base64 = dofile(script_root .. "/modules/base64.lua")
    dofile(script_root .. "/modules/utils.lua")

    local api_handle = assert(io.open(script_root .. "/apis/dandanplay.lua", "rb"))
    local api = api_handle:read("*a")
    api_handle:close()

    local appid_ciphertext = assert(api:match('local appid = "([^"]+)"'))
    local secret_ciphertext = assert(api:match('local app_accept = "([^"]+)"'))
    local key = table_to_zero_indexed({
        0x00,0x01,0x02,0x03,0x04,
        0x05,0x06,0x07,0x08,0x09,
        0x0a,0x0b,0x0c,0x0d,0x0e,
        0x0f,0x10,0x11,0x12,0x13,
        0x14,0x15,0x16,0x17,0x18,
        0x19,0x1a,0x1b,0x1c,0x1d,
        0x1e,0x1f
    })

    local appid = AES.ECB.decrypt(key, Base64.decode(appid_ciphertext))
    local secret = AES.ECB.decrypt(key, Base64.decode(secret_ciphertext))
    require_printable_ascii("AppId", appid)
    require_printable_ascii("AppSecret", secret)

    local marker_path = os.getenv("MPV_ENJOY_DANMAKU_VERIFY_MARKER")
    if not marker_path or marker_path == "" then
        error("MPV_ENJOY_DANMAKU_VERIFY_MARKER is required")
    end
    local marker_handle = assert(io.open(marker_path, "wb"))
    marker_handle:write("DANDANPLAY_LUA_CREDENTIALS_OK")
    marker_handle:close()

    msg.info(
        string.format(
            "DANDANPLAY_LUA_CREDENTIALS_OK appid_bytes=%d secret_bytes=%d",
            #appid,
            #secret
        )
    )
end

local ok, failure = xpcall(verify, debug.traceback)
if ok then
    mp.commandv("quit")
else
    msg.error(failure)
    mp.commandv("quit", "1")
end
