--[[
Google Drive 同期（KOReader プラグイン）

/mnt/us/extensions/gdrive/bin/sync.sh（rclone）を呼び出して、
Google Drive のフォルダを /mnt/us/gdrive に同期する。
  ・電源ボタンを押して画面がついたとき（スリープからの復帰時）に自動で同期する
    （KOReader の外＝Kindle 標準の画面での復帰は、常駐の wakesync.sh が同期する）
  ・それ以外はメニューの「今すぐ同期」で手動
メニュー: ツール → Google Drive 同期
]]

local Device = require("device")
local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")

local GD = "/mnt/us/extensions/gdrive"
local LOCAL_DIR = "/mnt/us/gdrive"

-- KOReader の起動中に1回だけ待ち受けの確認をするための印
local checked_once = false

local GDriveSync = WidgetContainer:extend{
    name = "gdrivesync",
    is_doc_only = false,
}

local function fileExists(path)
    local f = io.open(path, "r")
    if f then f:close() return true end
    return false
end

local function readState()
    local st = {}
    local f = io.open(GD .. "/state.txt", "r")
    if not f then return st end
    for line in f:lines() do
        local k, v = line:match("^([%w_]+)=(.*)$")
        if k then st[k] = v end
    end
    f:close()
    return st
end

local function summary(st)
    if not st.status then
        return _("まだ同期していません。")
    end
    local head
    if st.status == "ok" then
        head = _("同期しました")
    elseif st.status == "running" then
        head = _("同期中です")
    else
        head = _("同期できませんでした")
    end
    local lines = {
        head .. "（" .. (st.time or "") .. "）",
        string.format(_("追加 %s ・ 削除 %s ・ 更新 %s"), st.added or "0", st.deleted or "0", st.updated or "0"),
        string.format(_("Kindle のファイル: %s 件"), st.files or "?"),
    }
    if st.status ~= "ok" and st.message and st.message ~= "" then
        table.insert(lines, st.message)
    end
    return table.concat(lines, "\n")
end

local function ready()
    return fileExists(GD .. "/bin/rclone") and fileExists(GD .. "/rclone.conf")
end

-- 電源ボタンでの復帰時に同期するか（設定は Kindle 側の config.sh の SYNC_ON_WAKE）
local function syncOnWake()
    local f = io.open(GD .. "/config.sh", "r")
    if not f then return false end
    local on = true
    for line in f:lines() do
        local v = line:match("^SYNC_ON_WAKE=(%d)")
        if v then on = (v == "1") end
    end
    f:close()
    return on
end

local function sh(args)
    os.execute("sh " .. GD .. "/bin/" .. args .. " </dev/null >/dev/null 2>&1")
end

function GDriveSync:init()
    if not Device:isKindle() then return end
    self.ui.menu:registerToMainMenu(self)
    if not checked_once then
        checked_once = true
        -- Kindle の再起動で止まった待ち受けを、設定どおりに戻す
        if ready() then sh("wake.sh ensure --silent") end
    end
end

-- 電源ボタンで復帰したとき（KOReader が受け取るイベント）
function GDriveSync:onResume()
    if not ready() or not syncOnWake() or self.syncing then return end
    self.syncing = true
    self.before = readState().time
    -- 常駐の待ち受けも同時に動くが、sync.sh 側で二重には走らない
    UIManager:scheduleIn(2, function()
        sh("sync.sh --bg --wake")
        UIManager:scheduleIn(4, function() self:poll(0, true) end)
    end)
end

function GDriveSync:refreshFileBrowser()
    -- ファイルブラウザを開いているときは一覧を読み直す
    local ok = pcall(function()
        if self.ui.file_chooser then
            self.ui.file_chooser:refreshPath()
        end
    end)
    return ok
end

function GDriveSync:poll(count, quiet)
    local st = readState()
    if st.status == "running" and count < 200 then
        UIManager:scheduleIn(3, function() self:poll(count + 1, quiet) end)
        return
    end
    self.syncing = false
    if quiet then
        local changed = (tonumber(st.added) or 0) + (tonumber(st.deleted) or 0) + (tonumber(st.updated) or 0) > 0
        if st.time == self.before then
            -- 今回は同期しなかった（直前に同期済み・Wi-Fi なし）
            return
        elseif st.message == "synced" and changed then
            UIManager:show(InfoMessage:new{ text = summary(st), timeout = 5 })
        elseif st.status == "error" then
            UIManager:show(InfoMessage:new{ text = summary(st), timeout = 8 })
        end
    else
        UIManager:show(InfoMessage:new{ text = summary(st), timeout = 8 })
    end
    self:refreshFileBrowser()
end

function GDriveSync:syncNow()
    if self.syncing then
        UIManager:show(InfoMessage:new{ text = _("同期中です。少し待ってください。"), timeout = 3 })
        return
    end
    if not ready() then
        UIManager:show(InfoMessage:new{
            text = _("rclone が未設定です。PC で python kindlectl.py gdrive-setup を実行してください。"),
        })
        return
    end
    local NetworkMgr = require("ui/network/manager")
    NetworkMgr:runWhenOnline(function()
        self.syncing = true
        os.execute("sh " .. GD .. "/bin/sync.sh --bg </dev/null >/dev/null 2>&1")
        UIManager:show(InfoMessage:new{ text = _("Google Drive と同期しています…"), timeout = 3 })
        UIManager:scheduleIn(4, function() self:poll(0) end)
    end)
end

function GDriveSync:addToMainMenu(menu_items)
    menu_items.gdrivesync = {
        text = _("Google Drive 同期"),
        sorting_hint = "tools",
        sub_item_table = {
            {
                text = _("今すぐ同期"),
                keep_menu_open = false,
                callback = function() self:syncNow() end,
            },
            {
                text = _("前回の結果"),
                keep_menu_open = true,
                callback = function()
                    UIManager:show(InfoMessage:new{ text = summary(readState()) })
                end,
            },
            {
                text = _("電源ボタンで復帰したときに同期"),
                keep_menu_open = true,
                checked_func = function() return syncOnWake() end,
                callback = function()
                    if syncOnWake() then sh("wake.sh off --silent") else sh("wake.sh on --silent") end
                end,
            },
            {
                text = _("同期フォルダを開く"),
                enabled_func = function() return self.ui.file_chooser ~= nil end,
                callback = function()
                    pcall(function() self.ui.file_chooser:changeToPath(LOCAL_DIR) end)
                end,
            },
        },
    }
end

return GDriveSync
