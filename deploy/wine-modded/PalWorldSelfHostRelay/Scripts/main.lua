-- PalWorldSelfHost volatile chat-event adapter for Palworld 1.0 / UE4SS.
-- The host creates PALWORLD_GAME_HOOK_SPOOL on tmpfs. No bearer or Discord
-- credential enters the game process, and this hook performs no network I/O.

local spool = os.getenv("PALWORLD_GAME_HOOK_SPOOL") or ""
-- The shared environment uses the host's Linux tmpfs path. Wine exposes the
-- host root as Z:, so operators do not need a Windows path in that file.
if spool:sub(1, 1) == "/" then
    spool = "Z:" .. spool:gsub("/", "\\")
end
local counter = 0

local function escape_json(value)
    value = tostring(value or "")
    value = value:gsub("\\", "\\\\"):gsub('"', '\\"')
    value = value:gsub("\b", "\\b"):gsub("\f", "\\f")
    value = value:gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t")
    value = value:gsub("[%z\1-\31]", "")
    return value
end

local function bounded(value, maximum)
    value = tostring(value or "")
    if #value > maximum then value = value:sub(1, maximum) end
    return value
end

local function emit_chat(player, message, category)
    if spool == "" or category < 1 or category > 3 then return end
    counter = counter + 1
    local category_names = {"say", "guild", "global"}
    local body = string.format(
        '{"schema":1,"kind":"chat","player":"%s","message":"%s","category":"%s"}',
        escape_json(bounded(player, 80)),
        escape_json(bounded(message, 500)),
        category_names[category]
    )
    local stem = string.format("event-%d-%06d", os.time(), counter)
    local last = spool:sub(-1)
    local separator = (last == "/" or last == "\\") and "" or "\\"
    local temporary = spool .. separator .. stem .. ".tmp"
    local final = spool .. separator .. stem .. ".json"
    local handle = io.open(temporary, "wb")
    if not handle then return end
    handle:write(body)
    handle:close()
    os.rename(temporary, final)
end

if spool == "" then
    print("[PalWorldSelfHostRelay] disabled: PALWORLD_GAME_HOOK_SPOOL is empty")
else
    local ok, reason = pcall(function()
        RegisterHook("/Script/Pal.PalGameStateInGame:BroadcastChatMessage", function(_, parameter)
            pcall(function()
                local record = parameter:get()
                local category = tonumber(record.Category) or 0
                emit_chat(record.Sender:ToString(), record.Message:ToString(), category)
            end)
        end)
    end)
    if ok then
        print("[PalWorldSelfHostRelay] BroadcastChatMessage hook registered")
    else
        print("[PalWorldSelfHostRelay] hook unavailable: " .. tostring(reason))
    end
end
