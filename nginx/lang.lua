supported_langs = {["cs"]=true, ["de"]=true, ["en"]=true, ["es"]=true, ["fr"]=true, ["zh-CN"]=true}
fallback_lang = "en"


local lang_cookie = ngx.var.cookie_lang
if lang_cookie ~= nil then
    if supported_langs[lang_cookie] then
        return lang_cookie
    else
        return fallback_lang
    end
end


local lang_header = ngx.var.http_accept_language
if lang_header == nil then
    return fallback_lang
end

local cleaned = ngx.re.sub(lang_header, "^.*:", "")
local options = {}
local iterator, err = ngx.re.gmatch(cleaned, "\\s*([a-z]+(?:-[a-z])*)\\s*(?:;q=([0-9]+(.[0-9]*)?))?\\s*(,|$)", "i")
for m, err in iterator do
    local lang = m[1]
    local priority = 1
    if m[2] ~= nil then
        priority = tonumber(m[2])
        if priority == nil then
            priority = 1
        end
    end
    table.insert(options, {lang, priority})
end

table.sort(options, function(a, b) return b[2] < a[2] end)

for idx, option in pairs(options) do
    local lang = option[1]
    if supported_langs[lang] then
        return lang
    end
end

return fallback_lang
