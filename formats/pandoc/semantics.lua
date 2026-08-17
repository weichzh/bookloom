local function has_class(element, wanted)
  for _, class in ipairs(element.classes) do
    if class == wanted then
      return true
    end
  end
  return false
end

local function literal_inlines(value)
  local result = pandoc.Inlines({})
  for word, whitespace in value:gmatch("([^%s]+)(%s*)") do
    result:insert(pandoc.Str(word))
    if whitespace ~= "" then
      result:insert(pandoc.Space())
    end
  end
  return result
end

function Span(element)
  if has_class(element, "line-break") then
    if #element.content ~= 0 then
      error("line-break must be empty")
    end
    return pandoc.LineBreak()
  end

  if has_class(element, "cn-concept") then
    local original = element.attributes["data-original"]
    if not original or original == "" then
      error("cn-concept requires data-original")
    end
    local content = pandoc.Inlines({})
    content:extend(element.content)
    content:insert(pandoc.Str("（"))
    content:insert(pandoc.Emph(literal_inlines(original)))
    content:insert(pandoc.Str("）"))
    return pandoc.Span(
      content,
      pandoc.Attr("", { "cn-concept-rendered" }, {})
    )
  end

  if FORMAT == "json" then
    return element
  end

  if has_class(element, "source-page") then
    local page = element.attributes["data-page"]
    if not page or not page:match("^%d+$") then
      error("source-page requires a numeric data-page")
    end
    if FORMAT:match("html") or FORMAT:match("epub") then
      return pandoc.RawInline(
        "html",
        '<span class="source-page" data-page="' .. page .. '"></span>'
      )
    end
    if FORMAT == "typst" then
      return pandoc.RawInline("typst", '#metadata("source-page:' .. page .. '")')
    end
    if FORMAT:match("latex") then
      return pandoc.RawInline("latex", "\\sourcepage{" .. page .. "}")
    end
    return pandoc.Span({})
  end

  if has_class(element, "source-unit") then
    local unit = element.attributes["data-unit"]
    if not unit or not unit:match("^%d+$") then
      error("source-unit requires a numeric data-unit")
    end
    return pandoc.Inlines({})
  end

end
