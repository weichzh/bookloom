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

-- Single caption: ![Full caption](figure.png){alt="Short description"}
-- Multi-paragraph caption with independent alt:
-- :::: {#fig-one .native-figure}
-- [![Short description](figure.png)]{.source-image}
-- ::: {.figure-caption}
-- First caption paragraph.
--
-- Second caption paragraph.
-- :::
-- ::::
-- Empty source points and whitespace do not make a visible body or caption.
local function visible_text(value)
  for _, cp in utf8.codes(value or "") do
    local whitespace = (cp >= 0x09 and cp <= 0x0d) or cp == 0x20
      or cp == 0x85 or cp == 0xa0 or cp == 0x1680
      or (cp >= 0x2000 and cp <= 0x200f)
      or cp == 0x2028 or cp == 0x2029 or cp == 0x202f
      or cp == 0x205f or cp == 0x2060 or cp == 0x3000 or cp == 0xfeff
    if not whitespace then return true end
  end
  return false
end

local function meaningful_blocks(blocks)
  local meaningful = false
  local function check(value)
    if visible_text(value.text) then meaningful = true end
  end
  pandoc.Div(blocks):walk({
    Str = check,
    Code = check,
    CodeBlock = check,
    Math = check,
    Image = function() meaningful = true end,
    HorizontalRule = function() meaningful = true end,
  })
  return meaningful
end

local function has_explicit_hiding(element)
  local attributes = element.attributes or (element.attr and element.attr.attributes) or {}
  for key, value in pairs(attributes) do
    key, value = key:lower(), tostring(value):lower()
    if key == "hidden" or (key == "aria-hidden" and value:match("^%s*true%s*$")) then
      return true
    end
    if key == "style" then
      for declaration in value:gmatch("[^;]+") do
        local property, setting = declaration:match("^%s*([%w%-]+)%s*:%s*(.-)%s*$")
        if property then
          setting = setting:gsub("%s*!important%s*$", ""):match("^%s*(.-)%s*$")
          local opacity = setting:match("^([%d%.]+)%%?$")
          if (property == "display" and setting == "none")
              or (property == "visibility" and (setting == "hidden" or setting == "collapse"))
              or (property == "content-visibility" and setting == "hidden")
              or (property == "opacity" and opacity and tonumber(opacity) == 0) then
            return true
          end
        end
      end
    end
  end
  return false
end

local function reject_hidden_caption(element, meaningful)
  if has_explicit_hiding(element) and meaningful then
    error("native-figure blocks mode cannot hide caption content")
  end
end

-- Row and Cell attributes are not visited by Pandoc's Block/Inline walkers.
local function check_caption_table(table_element)
  local function check_rows(rows, parent)
    local parent_has_content = false
    for _, row in ipairs(rows) do
      local row_has_content = false
      for _, cell in ipairs(row.cells) do
        local content = meaningful_blocks(cell.contents)
        reject_hidden_caption(cell, content)
        row_has_content = row_has_content or content
      end
      reject_hidden_caption(row, row_has_content)
      parent_has_content = parent_has_content or row_has_content
    end
    reject_hidden_caption(parent, parent_has_content)
  end
  check_rows(table_element.head.rows, table_element.head)
  for _, body in ipairs(table_element.bodies) do
    local all_rows = pandoc.List({})
    all_rows:extend(body.head)
    all_rows:extend(body.body)
    check_rows(all_rows, body)
  end
  check_rows(table_element.foot.rows, table_element.foot)
end

local function validate_caption_visibility(caption)
  reject_hidden_caption(caption, meaningful_blocks(caption.content))
  caption:walk({
    Block = function(block)
      reject_hidden_caption(block, meaningful_blocks({block}))
    end,
    Inline = function(inline)
      reject_hidden_caption(inline, meaningful_blocks({pandoc.Plain({inline})}))
    end,
    Table = check_caption_table,
  })
end

-- Validate authored nodes before Span renders source-page markers to RawInline.
-- Legacy image mode and ordinary Divs retain their previous behavior.
local function validate_blocks_figure_source(element)
  if not has_class(element, "native-figure") or has_class(element, "source-unit")
      or element.attributes["data-figure-body"] ~= "blocks" then
    return nil
  end
  -- A hidden figure hides its caption regardless of the caption's own style.
  reject_hidden_caption(element, true)
  element:walk({
    RawBlock = function() error("native-figure blocks mode does not support RawBlock") end,
    RawInline = function() error("native-figure blocks mode does not support RawInline") end,
    Div = function(block)
      if has_class(block, "figure-caption") then validate_caption_visibility(block) end
    end,
  })
  return nil
end

local function native_figure(element)
  local mode = element.attributes["data-figure-body"]
  if mode ~= nil and mode ~= "blocks" then
    error("native-figure data-figure-body must be blocks when specified")
  end
  if mode == "blocks" then
    element:walk({
      Figure = function() error("native-figure cannot contain a nested Figure") end,
    })
  end
  local caption, caption_index = nil, nil
  for index, block in ipairs(element.content) do
    if block.t == "Div" and has_class(block, "figure-caption") then
      caption, caption_index = block, index
    end
  end
  local captions = 0
  pandoc.Div(element.content):walk({
    Div = function(block)
      if has_class(block, "figure-caption") then
        captions = captions + 1
      end
    end,
  })
  if captions ~= 1 or not caption then
    error("native-figure requires exactly one figure-caption Div")
  end
  if caption_index ~= #element.content then
    error("native-figure figure-caption must be the final block")
  end
  local caption_visible = mode == "blocks" and meaningful_blocks(caption.content)
    or (mode == nil and pandoc.utils.stringify(caption.content):match("%S"))
  if not caption_visible then
    error("native-figure figure-caption must not be empty")
  end
  local body = pandoc.Blocks({})
  for index = 1, caption_index - 1 do
    body:insert(element.content[index])
  end
  local images = 0
  pandoc.Div(body):walk({
    Figure = function() error("native-figure cannot contain a nested Figure") end,
    Image = function() images = images + 1 end,
  })
  caption:walk({
    Figure = function() error("native-figure cannot contain a nested Figure") end,
  })
  if mode == "blocks" then
    if not meaningful_blocks(body) then
      error("native-figure blocks body must be meaningful")
    end
  elseif images == 0 then
    error("native-figure body requires an image")
  end
  return pandoc.Figure(body, pandoc.Caption({ caption }), element.attr)
end

-- A title-only source unit owns its Header without creating an empty paragraph.
function Div(element)
  if has_class(element, "native-figure") and not has_class(element, "source-unit") then
    return native_figure(element)
  end
  if not has_class(element, "source-unit") then
    return nil
  end
  local unit = element.attributes["data-unit"]
  if not unit or not unit:match("^%d+$") then
    error("source-unit Div requires a numeric data-unit")
  end
  if element.identifier ~= "" or #element.classes ~= 1 then
    error("source-unit Div is metadata only; container ID or extra classes are not allowed")
  end
  for name, _ in pairs(element.attributes) do
    if name ~= "data-unit" then
      error("source-unit Div is metadata only; extra attributes are not allowed")
    end
  end
  if #element.content ~= 1 or element.content[1].t ~= "Header" then
    error("source-unit Div must contain exactly one Header")
  end
  if FORMAT == "json" then
    return element
  end
  return element.content
end

-- The first pass sees authored Raw nodes before output-specific source markers
-- are rendered. The second pass keeps the existing bottom-up semantics.
return {
  { traverse = "topdown", Div = validate_blocks_figure_source },
  { Span = Span, Div = Div },
}
