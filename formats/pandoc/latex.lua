local labels = {}
local unknown_raw = {}
local unresolved_refs = {}

local function has_class(element, wanted)
  for _, class in ipairs(element.classes or {}) do
    if class == wanted then
      return true
    end
  end
  return false
end

local function balanced(value, start, opening, closing)
  if value:sub(start, start) ~= opening then
    return nil
  end
  local depth = 0
  local escaped = false
  for index = start, #value do
    local char = value:sub(index, index)
    if escaped then
      escaped = false
    elseif char == "\\" then
      escaped = true
    elseif char == opening then
      depth = depth + 1
    elseif char == closing then
      depth = depth - 1
      if depth == 0 then
        return value:sub(start + 1, index - 1), index + 1
      end
    end
  end
  return nil
end

local function skip_space(value, position)
  while position <= #value and value:sub(position, position):match("%s") do
    position = position + 1
  end
  return position
end

local function command_args(value, command, count)
  local prefix = "\\" .. command
  local position = skip_space(value, 1)
  if value:sub(position, position + #prefix - 1) ~= prefix then
    return nil
  end
  position = position + #prefix
  local arguments = {}
  for _ = 1, count do
    position = skip_space(value, position)
    local argument, next_position = balanced(value, position, "{", "}")
    if not argument then
      return nil
    end
    table.insert(arguments, argument)
    position = next_position
  end
  if value:sub(skip_space(value, position)):match("%S") then
    return nil
  end
  return arguments
end

local function source_markers(value, block)
  local pages = {}
  local remaining = value:gsub("\\sourcepage%s*{%s*(%d+)%s*}", function(page)
    table.insert(pages, page)
    return ""
  end)
  if #pages == 0 or remaining:match("%S") then
    return nil
  end
  local parts = {}
  for _, page in ipairs(pages) do
    table.insert(parts, '<span class="source-page" data-page="' .. page .. '"></span>')
  end
  local html = table.concat(parts)
  return block and pandoc.RawBlock("html", html) or pandoc.RawInline("html", html)
end

local function latex_inlines(value, preserve_source_pages)
  value = value:gsub("\\greekfont%s*", "")
  local document = pandoc.read(value, "latex+raw_tex")
  if preserve_source_pages then
    document = document:walk({
      RawInline = function(inline)
        if inline.format ~= "latex" then
          return nil
        end
        local marker = source_markers(inline.text, false)
        if marker then
          return marker
        end
        if inline.text:match("^%s*\\sourcepage") then
          error("cnconcept sourcepage requires a numeric page argument")
        end
      end,
    })
  end
  local result = pandoc.Inlines({})
  for _, block in ipairs(document.blocks) do
    if block.t == "Para" or block.t == "Plain" then
      result:extend(block.content)
    else
      result:insert(pandoc.Str(pandoc.utils.stringify(block)))
    end
  end
  return result
end

local layout_arity = {
  addcontentsline = 3,
  addtocounter = 2,
  captionsetup = 1,
  enlargethispage = 1,
  markboth = 2,
  markright = 1,
  pagestyle = 1,
  pdfbookmark = 2,
  refstepcounter = 1,
  setcounter = 2,
  setlength = 2,
  stepcounter = 1,
  thispagestyle = 1,
  vspace = 1,
  hspace = 1,
}

local layout_redefinitions = {
  ["\\arraystretch"] = true,
  ["\\labelenumi"] = true,
  ["\\labelenumii"] = true,
  ["\\labelitemi"] = true,
  ["\\labelitemii"] = true,
}

local layout_switches = {
  backmatter = true,
  appendix = true,
  begingroup = true,
  boldmath = true,
  bigskip = true,
  ["break"] = true,
  allowbreak = true,
  arraybackslash = true,
  centering = true,
  clearpage = true,
  cleardoublepage = true,
  endgroup = true,
  enspace = true,
  FloatBarrier = true,
  footnotesize = true,
  frontmatter = true,
  greekfont = true,
  hfill = true,
  hrule = true,
  huge = true,
  Huge = true,
  LARGE = true,
  indent = true,
  large = true,
  Large = true,
  linebreak = true,
  mainmatter = true,
  medskip = true,
  newpage = true,
  nobreak = true,
  nopagebreak = true,
  noindent = true,
  normalfont = true,
  normalsize = true,
  pagebreak = true,
  par = true,
  phantomsection = true,
  protect = true,
  quad = true,
  qquad = true,
  raggedcolumns = true,
  raggedleft = true,
  small = true,
  scriptsize = true,
  smallskip = true,
  sloppy = true,
  tableofcontents = true,
  tiny = true,
  maketitle = true,
  vfill = true,
}

local function layout_only(value)
  local position = skip_space(value, 1)
  if value:match("^%s*\\hangafter%s*=%s*1%s*$")
    or value:match("^%s*\\hangindent%s*=%s*1%.5em%s*$") then
    return true
  end
  if value:sub(position, position + 3) == "\\let" then
    return value:sub(position):match("^\\let%s*\\[%a@]+%s*\\[%a@]+%s*$") ~= nil
  end
  local name = value:sub(position):match("^\\([%a@]+)")
  if not name then
    return false
  end
  position = position + #name + 1
  if value:sub(position, position) == "*" then
    position = position + 1
  end
  position = skip_space(value, position)
  if value:sub(position, position + 1) == "{}" then
    position = skip_space(value, position + 2)
  end
  if name == "renewcommand" then
    local target, next_position = balanced(value, position, "{", "}")
    if not next_position or not layout_redefinitions[target:gsub("%s+", "")] then
      return false
    end
    position = skip_space(value, next_position)
    local _, body_end = balanced(value, position, "{", "}")
    return body_end ~= nil and not value:sub(skip_space(value, body_end)):match("%S")
  end
  if value:sub(position, position) == "[" then
    local _, next_position = balanced(value, position, "[", "]")
    if not next_position then
      return false
    end
    position = skip_space(value, next_position)
  end
  if layout_switches[name] then
    return not value:sub(position):match("%S")
  end
  local arity = layout_arity[name]
  if not arity then
    return false
  end
  for _ = 1, arity do
    local _, next_position = balanced(value, position, "{", "}")
    if not next_position then
      return false
    end
    position = skip_space(value, next_position)
  end
  return not value:sub(position):match("%S")
end

local function normalize_table_scroll(document)
  local layout = {
    centering = true, raggedleft = true, raggedright = true,
    small = true, footnotesize = true, scriptsize = true,
    normalsize = true, large = true, Large = true, LARGE = true,
    phantomsection = true,
  }
  return document:walk({
    RawBlock = function(block)
      if block.format ~= "latex" then
        return nil
      end
      local _, opening = block.text:find("^%s*\\begin%s*{epubtablescroll}")
      if not opening then
        return nil
      end
      local closing = block.text:find("\\end%s*{epubtablescroll}%s*$", opening + 1)
      if not closing then
        error("LaTeX epubtablescroll 环境未闭合")
      end
      local inner = block.text:sub(opening + 1, closing - 1)
      if inner:find("\\begin%s*{epubtablescroll}") then
        error("LaTeX epubtablescroll 不支持嵌套")
      end
      local table_start, table_open_end, environment = inner:find("\\begin%s*{(table%*?)}")
      if not table_start then
        table_start, table_open_end, environment = inner:find("\\begin%s*{(longtable)}")
      end
      local table_end
      if table_start then
        local pattern = "\\end%s*{" .. environment:gsub("(%W)", "%%%1") .. "}"
        local position = table_open_end + 1
        while true do
          local _, ending = inner:find(pattern, position)
          if not ending then break end
          table_end, position = ending, ending + 1
        end
      end
      local table_source = table_start and table_end and inner:sub(table_start, table_end) or ""
      local function transparent_raw(raw)
        if raw.format ~= "latex" then
          return false
        end
        if source_markers(raw.text, false) or command_args(raw.text, "label", 1) then
          return true
        end
        local name = raw.text:match("^%s*\\([%a@]+)")
        if (layout[name] or name == "setlength") and layout_only(raw.text) then
          return true
        end
        for _, command in ipairs({ "setcounter", "addtocounter", "stepcounter", "refstepcounter" }) do
          local args = command_args(raw.text, command,
            (command == "setcounter" or command == "addtocounter") and 2 or 1)
          local target = args and args[1]:match("^%s*(.-)%s*$")
          if target and target ~= "" and target ~= "figure" and target ~= "table"
            and table_source:find(raw.text, 1, true) ~= nil then
            return true
          end
        end
        -- Pandoc may move a format declaration from inside a table to a
        -- preceding raw node; retain it for the existing counter collector.
        return command_args(raw.text, "epubcounterformat", 2)
          and table_source:find(raw.text, 1, true) ~= nil
      end
      local function transparent_block(candidate)
        if candidate.t == "RawBlock" then
          return transparent_raw(candidate)
        elseif candidate.t == "Plain" or candidate.t == "Para" then
          for _, inline in ipairs(candidate.content) do
            if inline.t == "RawInline" then
              if not transparent_raw(inline) then
                return false
              end
            elseif inline.t ~= "Space" and inline.t ~= "SoftBreak" then
              return false
            end
          end
          return true
        end
        return false
      end
      local parsed = pandoc.read(inner, "latex+raw_tex")
      -- Pandoc can lift an original float's minipage note out beside its Table.
      -- Validate the material outside the source environment, not the lifted
      -- AST shape, so the original note stays complete and in source order.
      if table_source ~= "" then
        for _, outside in ipairs({ inner:sub(1, table_start - 1), inner:sub(table_end + 1) }) do
          for _, candidate in ipairs(pandoc.read(outside, "latex+raw_tex").blocks) do
            if not transparent_block(candidate) then
              error("LaTeX epubtablescroll 的原表之外只能有透明定位／版式声明")
            end
          end
        end
      end
      local count = 0
      for _, candidate in ipairs(parsed.blocks) do
        if candidate.t == "Table" then
          count = count + 1
        elseif table_source == "" and not transparent_block(candidate) then
          error("LaTeX epubtablescroll 只能包含一个原生 Table 和透明定位／版式声明")
        end
      end
      if count ~= 1 then
        error("LaTeX epubtablescroll 必须恰有一个原生 Table")
      end
      return pandoc.Div(parsed.blocks, pandoc.Attr("", { "table-scroll" }))
    end,
  })
end

local function remember_unknown(value)
  value = value:gsub("^%s+", ""):gsub("%s+$", "")
  if value ~= "" then
    unknown_raw[value] = true
  end
end

local function normalized_math_layout(value)
  local prefix = "\\resizebox"
  local position = skip_space(value, 1)
  if value:sub(position, position + #prefix - 1) ~= prefix then
    return nil
  end
  position = skip_space(value, position + #prefix)
  local width, next_position = balanced(value, position, "{", "}")
  if not width or width:gsub("%s+", "") ~= "0.90\\textwidth" then
    return nil
  end
  position = skip_space(value, next_position)
  local height
  height, next_position = balanced(value, position, "{", "}")
  if not height or height:gsub("%s+", "") ~= "!" then
    return nil
  end
  position = skip_space(value, next_position)
  local body
  body, next_position = balanced(value, position, "{", "}")
  if not body or value:sub(skip_space(value, next_position)):match("%S") then
    return nil
  end
  body = body:gsub("^%s+", ""):gsub("%s+$", "")
  if body:sub(1, 1) ~= "$" or body:sub(-1) ~= "$" then
    return nil
  end
  return body:sub(2, -2)
end

local function semantic_raw(element, block)
  if element.format ~= "latex" then
    return nil
  end
  local marker = source_markers(element.text, block)
  if marker then
    return marker
  end
  local concept = command_args(element.text, "cnconcept", 2)
  if concept then
    local content = latex_inlines(concept[1], true)
    content:insert(pandoc.Space())
    content:insert(pandoc.Str("（"))
    content:insert(pandoc.Emph(latex_inlines(concept[2], true)))
    content:insert(pandoc.Str("）"))
    local span = pandoc.Span(
      content,
      pandoc.Attr("", { "cn-concept-rendered" }, {
        ["data-original"] = pandoc.utils.stringify(latex_inlines(concept[2], true)),
      })
    )
    return block and pandoc.Para({ span }) or span
  end
  local fallback = command_args(element.text, "epubfallback", 3)
  if fallback then
    local alt = fallback[2]:gsub("^%s+", ""):gsub("%s+$", "")
    local image = pandoc.Image({ pandoc.Str(alt) }, fallback[1], "")
    return block and pandoc.Para({ image }) or image
  end
  local marker = element.text:gsub("%%", ""):gsub("%s+", "")
  if marker == "\\tikz[baseline=-0.31em]{"
      .. "\\fill[black,roundedcorners=0.11em](0,0)rectangle(0.55em,0.55em);"
      .. "\\fill[white](0.275em,0.275em)circle(0.10em);}" then
    local content = pandoc.Span(
      { pandoc.Str("▣") },
      pandoc.Attr("", { "figure-marker" }, { ["aria-hidden"] = "true" })
    )
    return block and pandoc.Para({ content }) or content
  end
  local question = element.text:match("^%s*\\noindent%s+([1-9])%s*$")
  if question then
    local content = pandoc.Str(question)
    return block and pandoc.Para({ content }) or content
  end
  if element.text:match("^%s*\\dotfill%s+%d+%s*$") then
    return block and pandoc.Blocks({}) or pandoc.Inlines({})
  end
  local boxed = command_args(element.text, "mbox", 1)
  if boxed then
    local content = latex_inlines(boxed[1])
    return block and pandoc.Para(content) or content
  end
  if command_args(element.text, "epubcounterformat", 2) then
    return block and pandoc.Blocks({}) or pandoc.Inlines({})
  end
  local indented = element.text:match("^%s*\\\\qquad%s*(.-)%s*\\\\par%s*$")
  if indented and indented:match("%S") then
    local content = latex_inlines(indented)
    return block and pandoc.Para(content) or content
  end
  local label = command_args(element.text, "label", 1)
  if label then
    labels[label[1]] = labels[label[1]] or { id = label[1], display = label[1] }
    local anchor = pandoc.Span({}, pandoc.Attr(label[1]))
    return block and pandoc.Para({ anchor }) or anchor
  end
  for _, command in ipairs({ "ref", "eqref", "autoref", "pageref" }) do
    local reference = command_args(element.text, command, 1)
    if reference then
      local target = labels[reference[1]]
      if not target then
        unresolved_refs[reference[1]] = true
        return pandoc.Str("?")
      end
      if not target.content or (#target.content == 1 and target.content[1].t == "Str") then
        local display = target.display
        if command == "eqref" then
          display = "(" .. display .. ")"
        end
        local link = pandoc.Link({ pandoc.Str(display) }, "#" .. target.id)
        return block and pandoc.Para({ link }) or link
      end
      local content = pandoc.Inlines({})
      if command == "eqref" then
        content:insert(pandoc.Str("("))
      end
      if target.content then
        content:extend(target.content)
      else
        content:insert(pandoc.Str(target.display))
      end
      if command == "eqref" then
        content:insert(pandoc.Str(")"))
      end
      local link = pandoc.Link(content, "#" .. target.id)
      return block and pandoc.Para({ link }) or link
    end
  end
  if element.text:match("^%s*\\includepdf") or layout_only(element.text) then
    return block and pandoc.Blocks({}) or pandoc.Inlines({})
  end
  remember_unknown(element.text)
  return block and pandoc.Blocks({}) or pandoc.Inlines({})
end

local function fallback_image_alt(element)
  local alt = pandoc.utils.stringify(element.caption):lower()
  if alt:match("%S") and alt ~= "image" and alt ~= "figure" then
    return element
  end
  local source = (element.src or ""):gsub("\\\\", "/"):match("([^/]+)$") or ""
  source = source:gsub("%.[^%.]+$", ""):gsub("[-_]+", " ")
  if source:match("%S") then
    element.caption = pandoc.Inlines({ pandoc.Str("图像：" .. source) })
  end
  return element
end

local function explicit_math_tag(value)
  local _, opening = value:find("\\tag%*?%s*{")
  if not opening then
    return nil
  end
  local tag = balanced(value, opening, "{", "}")
  if tag == nil then
    error("LaTeX 公式 tag 参数未闭合")
  end
  return tag
end

local function math_rows(value)
  local rows = {}
  local start = 1
  local position = 1
  local braces = 0
  local environments = 0
  while position <= #value do
    local char = value:sub(position, position)
    if char == "\\" then
      local begin = value:sub(position):match("^(\\begin%s*{[^{}]+})")
      local finish = value:sub(position):match("^(\\end%s*{[^{}]+})")
      if begin then
        environments = environments + 1
        position = position + #begin
      elseif finish then
        environments = environments - 1
        position = position + #finish
      elseif value:sub(position + 1, position + 1) == "\\"
        and braces == 0 and environments == 0 then
        local after = position + 2
        if value:sub(after, after) == "*" then
          after = after + 1
        end
        if value:sub(after, after) == "[" then
          local _, next_position = balanced(value, after, "[", "]")
          after = next_position or after
        end
        rows[#rows + 1] = { text = value:sub(start, position - 1), separator = value:sub(position, after - 1) }
        position = after
        start = after
      else
        position = position + 2
      end
    else
      if char == "{" then
        braces = braces + 1
      elseif char == "}" then
        braces = braces - 1
      end
      position = position + 1
    end
  end
  rows[#rows + 1] = { text = value:sub(start), separator = "" }
  return rows
end

local function collect_labels(document)
  local has_part = pandoc.utils.stringify(
    document.meta["translator-latex-has-part"] or ""
  ) == "true"
  local chapter_level = has_part and 2 or 1
  local figure_label = pandoc.utils.stringify(document.meta["translator-latex-caption-figure"] or "")
  local table_label = pandoc.utils.stringify(document.meta["translator-latex-caption-table"] or "")
  local number_captions = figure_label ~= "" or table_label ~= ""
  if number_captions and (figure_label == "" or table_label == "") then
    error("LaTeX EPUB 原生图表编号缺少语言标签")
  end
  local section_level = chapter_level + 1
  local subsection_level = section_level + 1
  local part = 0
  local in_part = false
  local chapter = 0
  local in_appendix = false
  local appendix_sections = {}
  local chapter_display
  local section = 0
  local subsection = 0
  local figure = 0
  local table_number = 0
  local equation = 0
  local pending_counter
  local current_label
  local counter_formats = {}
  local thefigure_values = {}
  local thetable_values = {}

  local function unnumbered_caption(caption)
    local short = caption.short
    if short and #short == 1 and short[1].t == "RawInline"
      and short[1].format == "latex"
      and short[1].text == "\\epubcaptionunnumbered" then
      return true
    end
    return false
  end

  local function caption_has_text(caption)
    local preview = pandoc.Pandoc(caption.long):walk({
      Image = function() return pandoc.Str("x") end,
      RawInline = function(inline)
        if inline.format == "latex" then
          if inline.text:match("^%s*\\thefigure%s*$")
            or inline.text:match("^%s*\\thetable%s*$") then
            return pandoc.Str("x")
          end
          if command_args(inline.text, "epubfallback", 3)
            or command_args(inline.text, "cnconcept", 2) then
            return pandoc.Str("x")
          end
          for _, command in ipairs({ "ref", "eqref", "autoref", "pageref" }) do
            if command_args(inline.text, command, 1) then
              return pandoc.Str("x")
            end
          end
        end
      end,
    })
    return pandoc.utils.stringify(preview.blocks):match("%S") ~= nil
  end

  local function bind_caption_labels(caption, display)
    local function bind(raw)
      if raw.format == "latex" then
        local label = command_args(raw.text, "label", 1)
        if label then
          labels[label[1]] = { id = label[1], display = display }
        end
      end
    end
    pandoc.Pandoc(caption.long):walk({ RawInline = bind, RawBlock = bind })
  end

  local function prefix_caption(caption, label, number)
    -- stringify omits raw TeX, so resolve the existing thefigure macro in a
    -- temporary copy before deciding whether a visible prefix is already present.
    local preview = pandoc.Pandoc(caption.long):walk({
      RawInline = function(inline)
        if inline.format == "latex" and (inline.text:match("^%s*\\thefigure%s*$")
          or inline.text:match("^%s*\\thetable%s*$")) then
          return pandoc.Inlines({ pandoc.Str(number), pandoc.Space() })
        end
      end,
    })
    local content = pandoc.utils.stringify(preview.blocks):gsub("^%s+", "")
    local existing_label, rest
    for _, candidate in ipairs({ "图", "表", "Figure", "Table", "Fig.", "Fig" }) do
      if content:sub(1, #candidate):lower() == candidate:lower() then
        existing_label = candidate
        rest = content:sub(#candidate + 1):gsub("^%s+", "")
        break
      end
    end
    if existing_label then
      local existing_figure = existing_label == "图" or existing_label == "Figure"
        or existing_label == "Fig" or existing_label == "Fig."
      local expected_figure = label == "图" or label == "Figure"
      local tail = rest:sub(#number + 1)
      local same_number = rest:sub(1, #number) == number
        and not tail:match("^[%w]") and not tail:match("^%.%d") and not tail:match("^%(") and not tail:match("^%-%w")
      local enclosed = "(" .. number .. ")"
      local enclosed_tail = rest:sub(#enclosed + 1)
      same_number = same_number or (rest:sub(1, #enclosed) == enclosed
        and not enclosed_tail:match("^[%w]") and not enclosed_tail:match("^%.%d") and not enclosed_tail:match("^%-%w"))
      local existing_number = rest:match("^%(([%w%.%-]+)%)")
        or rest:match("^(%d[%w%.%-]*%b())")
        or rest:match("^([A-Z]%.%d[%w%.%-]*%b())")
        or rest:match("^(%d[%w%.%-]*)") or rest:match("^([A-Z]%.%d[%w%.%-]*)")
        or rest:match("^([ivxlcdmIVXLCDM]+)%f[%W]")
        or rest:match("^([A-Za-z])%f[%W]")
      if existing_number then
        existing_number = existing_number:gsub("%.$", "")
      end
      if same_number and existing_figure == expected_figure then
        return
      elseif existing_number then
        error("LaTeX EPUB 原生图注已有不同编号：" .. existing_label .. " "
          .. existing_number .. "，来源计数为 " .. label .. " " .. number)
      end
    end
    local long = caption.long
    local first = long[1]
    if not first or (first.t ~= "Plain" and first.t ~= "Para") then
      error("LaTeX EPUB 原生图注首块不是段落")
    end
    local prefixed = pandoc.Inlines({ pandoc.Str(label), pandoc.Space(),
      pandoc.Str(number), pandoc.Space() })
    prefixed:extend(first.content)
    first.content = prefixed
    long[1] = first
    caption.long = long
  end

  local function number_header(block, display)
    local content = pandoc.Inlines({
      pandoc.Span(
        { pandoc.Str(display) },
        pandoc.Attr("", { "header-section-number" })
      ),
      pandoc.Space(),
    })
    content:extend(block.content)
    block.content = content
    block.classes:insert("unnumbered")
  end

  local function scoped(number)
    return chapter > 0 and (chapter_display() .. "." .. number) or tostring(number)
  end

  local function counter_value(name)
    if name == "chapter" then
      return chapter
    elseif name == "section" then
      return section
    elseif name == "figure" then
      return figure
    elseif name == "table" then
      return table_number
    elseif name == "equation" then
      return equation
    end
    return nil
  end

  local function set_counter(name, value)
    if name == "chapter" then
      chapter = value
    elseif name == "section" then
      section = value
      if in_appendix then
        appendix_sections[1] = value
      end
    elseif name == "figure" then
      figure = value
    elseif name == "table" then
      table_number = value
    elseif name == "equation" then
      equation = value
    end
  end

  local function alpha(number, upper)
    if number < 1 then
      error("LaTeX EPUB 字母计数器必须大于零")
    end
    local result = ""
    while number > 0 do
      number = number - 1
      local base = upper and 65 or 97
      result = string.char(base + number % 26) .. result
      number = math.floor(number / 26)
    end
    return result
  end

  chapter_display = function()
    return in_appendix and alpha(chapter, true) or tostring(chapter)
  end

  local function roman(number)
    if number < 1 or number > 3999 then
      error("LaTeX EPUB 罗马数字计数器必须在 1..3999")
    end
    local values = {
      { 1000, "m" }, { 900, "cm" }, { 500, "d" }, { 400, "cd" },
      { 100, "c" }, { 90, "xc" }, { 50, "l" }, { 40, "xl" },
      { 10, "x" }, { 9, "ix" }, { 5, "v" }, { 4, "iv" }, { 1, "i" },
    }
    local result = ""
    for _, pair in ipairs(values) do
      while number >= pair[1] do
        result = result .. pair[2]
        number = number - pair[1]
      end
    end
    return result
  end

  local function formatted_counter(name)
    local value = counter_value(name)
    local format = counter_formats[name]
    if not format then
      return scoped(value)
    end
    local rendered = format
    local styles = {
      arabic = function(number) return tostring(number) end,
      alph = function(number) return alpha(number, false) end,
      Alph = function(number) return alpha(number, true) end,
      roman = function(number) return roman(number) end,
      Roman = function(number) return roman(number):upper() end,
    }
    for style, formatter in pairs(styles) do
      rendered = rendered:gsub(
        "\\" .. style .. "%s*{%s*([%a]+)%s*}",
        function(counter)
          local counter_number = counter_value(counter)
          if counter_number == nil then
            error("LaTeX EPUB 不支持计数器：" .. counter)
          end
          return formatter(counter_number)
        end
      )
    end
    if rendered:find("\\") or rendered:find("[{}]") then
      error("LaTeX EPUB 不支持计数格式：" .. rendered)
    end
    return rendered
  end

  local function numeric(value, command)
    if not value:match("^%s*%-?%d+%s*$") then
      error("LaTeX EPUB " .. command .. " 只支持整数：" .. value)
    end
    return tonumber(value)
  end

  local function apply_counter_raw(value)
    local switch = value:gsub("%s+", ""):gsub("{}$", "")
    if switch == "\\frontmatter" or switch == "\\mainmatter"
      or switch == "\\backmatter" then
      in_part = false
      return
    end
    if switch == "\\appendix" then
      in_appendix = true
      in_part = false
      chapter = 0
      section = 0
      subsection = 0
      figure = 0
      table_number = 0
      equation = 0
      appendix_sections = {}
      pending_counter = nil
      return
    end
    local format = command_args(value, "epubcounterformat", 2)
    if format then
      if counter_value(format[1]) == nil then
        error("LaTeX EPUB 不支持计数器格式：" .. format[1])
      end
      counter_formats[format[1]] = format[2]
      return
    end
    local operation = command_args(value, "setcounter", 2)
    if operation and counter_value(operation[1]) ~= nil then
      set_counter(operation[1], numeric(operation[2], "setcounter"))
      return
    end
    operation = command_args(value, "addtocounter", 2)
    if operation and counter_value(operation[1]) ~= nil then
      set_counter(
        operation[1],
        counter_value(operation[1]) + numeric(operation[2], "addtocounter")
      )
      return
    end
    for _, command in ipairs({ "stepcounter", "refstepcounter" }) do
      operation = command_args(value, command, 1)
      if operation and counter_value(operation[1]) ~= nil then
        set_counter(operation[1], counter_value(operation[1]) + 1)
        if in_appendix and operation[1] == "chapter" then
          section = 0
          subsection = 0
          figure = 0
          table_number = 0
          equation = 0
          appendix_sections = {}
        elseif in_appendix and operation[1] == "section" then
          subsection = 0
          for index = #appendix_sections, 2, -1 do
            appendix_sections[index] = nil
          end
        end
        if command == "refstepcounter" then
          pending_counter = formatted_counter(operation[1])
          current_label = pending_counter
        end
        return
      end
    end
  end

  local scan_blocks

  local function scan_inlines(inlines)
    for _, inline in ipairs(inlines) do
      if inline.t == "RawInline" and inline.format == "latex" then
        apply_counter_raw(inline.text)
        if inline.text:match("^%s*\\thefigure%s*$") then
          table.insert(thefigure_values, formatted_counter("figure"))
        elseif number_captions and inline.text:match("^%s*\\thetable%s*$") then
          table.insert(thetable_values, formatted_counter("table"))
        end
        local label = command_args(inline.text, "label", 1)
        if label then
          labels[label[1]] = {
            id = label[1],
            display = pending_counter or label[1],
          }
          pending_counter = nil
        end
      elseif inline.t == "Math" and inline.mathtype == "DisplayMath" then
        -- Native amsmath environments advance on automatically numbered rows,
        -- not on labels. Explicit tags do not advance the amsmath counter.
        local prefix, environment = inline.text:match("^(%s*\\begin%s*{([%a%*]+)})")
        local body = inline.text
        local suffix = ""
        local supported = environment == "equation" or environment == "equation*"
          or environment == "align" or environment == "align*" or environment == "aligned"
        if supported then
          local escaped = environment:gsub("%*", "%%*")
          local ending = inline.text:find("\\end%s*{" .. escaped .. "}%s*$")
          if ending then
            body = inline.text:sub(#prefix + 1, ending - 1)
            suffix = inline.text:sub(ending)
          else
            supported = false
          end
        end
        if not supported then
          local math_labels = {}
          for label in inline.text:gmatch("\\label%s*{([^{}]+)}") do
            table.insert(math_labels, label)
          end
          if #math_labels > 0 then
            local math_tags = {}
            for tag in inline.text:gmatch("\\tag%s*{([^{}]+)}") do
              table.insert(math_tags, tag)
            end
            for index, label in ipairs(math_labels) do
              equation = equation + 1
              labels[label] = {
                id = label,
                display = math_tags[index] or formatted_counter("equation"),
              }
            end
          end
        else
          local rows = environment ~= "equation" and environment ~= "equation*"
            and math_rows(body) or { { text = body, separator = "" } }
          local rendered = {}
          for _, row in ipairs(rows) do
            local tag = explicit_math_tag(row.text)
            local suppressed = row.text:match("\\notag%f[^%a]")
              or row.text:match("\\nonumber%f[^%a]")
            local automatic = supported and (environment == "equation" or environment == "align")
              and not tag and not suppressed
            if automatic then
              equation = equation + 1
              tag = formatted_counter("equation")
              row.text = row.text .. "\n\\tag{" .. tag .. "}"
            end
            for label in row.text:gmatch("\\label%s*{([^{}]+)}") do
              local tag_content = tag and latex_inlines(tag) or nil
              labels[label] = {
                id = label,
                display = tag_content and pandoc.utils.stringify(tag_content) or current_label or label,
                content = tag_content,
              }
            end
            rendered[#rendered + 1] = row.text .. row.separator
          end
          inline.text = prefix .. table.concat(rendered) .. suffix
        end
      elseif inline.t == "Note" then
        scan_blocks(inline.content)
      elseif inline.content then
        scan_inlines(inline.content)
      elseif inline.caption then
        scan_inlines(inline.caption)
      end
    end
  end

  scan_blocks = function(blocks)
    for _, block in ipairs(blocks) do
      if block.t == "Header" then
        -- Pandoc raises every chapter to H2 as soon as a part occurs anywhere.
        -- Count using those reader levels, but nest only under an active part.
        local reader_level = block.level
        if has_part then
          if reader_level == 1 then
            in_part = true
          elseif not in_part then
            block.level = reader_level - 1
          end
        end
        local custom_display
        if not has_class(block, "unnumbered") then
          if has_part and reader_level == 1 then
            part = part + 1
            custom_display = tostring(part)
          elseif reader_level == chapter_level then
            chapter = chapter + 1
            section = 0
            subsection = 0
            figure = 0
            table_number = 0
            equation = 0
            pending_counter = nil
            appendix_sections = {}
            if has_part or in_appendix then
              custom_display = chapter_display()
            end
          elseif in_appendix and reader_level > chapter_level then
            local depth = reader_level - chapter_level
            appendix_sections[depth] = (appendix_sections[depth] or 0) + 1
            for index = depth + 1, #appendix_sections do
              appendix_sections[index] = nil
            end
            section = appendix_sections[1] or 0
            subsection = appendix_sections[2] or 0
            local parts = { chapter_display() }
            for index = 1, depth do
              parts[#parts + 1] = tostring(appendix_sections[index] or 0)
            end
            custom_display = depth == 1 and counter_formats.section
              and formatted_counter("section") or table.concat(parts, ".")
          elseif reader_level == section_level then
            section = section + 1
            subsection = 0
            if counter_formats.section then
              custom_display = formatted_counter("section")
            elseif has_part then
              custom_display = chapter .. "." .. section
            end
          elseif reader_level == subsection_level then
            subsection = subsection + 1
            if has_part then
              custom_display = chapter .. "." .. section .. "." .. subsection
            end
          end
          if custom_display then
            current_label = custom_display
            number_header(block, custom_display)
          elseif reader_level == chapter_level then
            current_label = tostring(chapter)
          elseif reader_level == section_level then
            current_label = chapter .. "." .. section
          elseif reader_level == subsection_level then
            current_label = chapter .. "." .. section .. "." .. subsection
          end
        end
        if block.identifier ~= "" then
          local display = pandoc.utils.stringify(block.content)
          if custom_display then
            display = custom_display
          elseif not has_class(block, "unnumbered") then
            if reader_level == chapter_level then
              display = tostring(chapter)
            elseif reader_level == section_level then
              display = chapter .. "." .. section
            elseif reader_level == subsection_level then
              display = chapter .. "." .. section .. "." .. subsection
            end
          end
          labels[block.identifier] = { id = block.identifier, display = display }
        end
      elseif block.t == "Figure" then
        local caption = block.caption
        local unnumbered = number_captions and unnumbered_caption(caption)
        if unnumbered then
          caption = pandoc.Caption(caption.long)
        end
        local numbered = not number_captions
          or (not unnumbered and caption_has_text(caption))
        if numbered then
          figure = figure + 1
        end
        local saved_format = counter_formats.figure
        scan_blocks(caption.long)
        scan_blocks(block.content)
        local display = ""
        if number_captions or block.identifier ~= "" then
          display = numbered and formatted_counter("figure") or (current_label or block.identifier)
        end
        if number_captions and numbered then
          bind_caption_labels(caption, display)
          prefix_caption(caption, figure_label, display)
          current_label = display
        end
        if number_captions then
          block.caption = caption
        end
        if block.identifier ~= "" then
          labels[block.identifier] = {
            id = block.identifier,
            display = display,
          }
        end
        counter_formats.figure = saved_format
      elseif block.t == "Table" then
        local caption = block.caption
        local saved_format = counter_formats.table
        local unnumbered = number_captions and unnumbered_caption(caption)
        if unnumbered then
          caption = pandoc.Caption(caption.long)
        end
        local numbered = not number_captions
          or (not unnumbered and caption_has_text(caption))
        if numbered then
          table_number = table_number + 1
        end
        if number_captions then
          scan_blocks(caption.long)
        end
        local display = ""
        if number_captions or block.identifier ~= "" then
          display = numbered and formatted_counter("table") or (current_label or block.identifier)
        end
        if number_captions and numbered then
          bind_caption_labels(caption, display)
          prefix_caption(caption, table_label, display)
          current_label = display
        end
        if number_captions then
          block.caption = caption
          counter_formats.table = saved_format
        end
        if block.identifier ~= "" then
          labels[block.identifier] = {
            id = block.identifier,
            display = display,
          }
        end
      elseif block.t == "RawBlock" and block.format == "latex" then
        apply_counter_raw(block.text)
      elseif block.t == "Para" or block.t == "Plain" then
        scan_inlines(block.content)
      elseif block.t == "LineBlock" then
        for _, line in ipairs(block.content) do
          scan_inlines(line)
        end
      elseif block.t == "Div" or block.t == "BlockQuote" then
        scan_blocks(block.content)
      elseif block.t == "BulletList" or block.t == "OrderedList" then
        for _, item in ipairs(block.content) do
          scan_blocks(item)
        end
      elseif block.t == "DefinitionList" then
        for _, item in ipairs(block.content) do
          scan_inlines(item[1])
          for _, definition in ipairs(item[2]) do
            scan_blocks(definition)
          end
        end
      end
    end
  end

  scan_blocks(document.blocks)
  local thefigure_index = 0
  local thetable_index = 0
  local function anchor_math_labels(paragraph)
    local content = pandoc.Inlines({})
    local changed = false
    for _, inline in ipairs(paragraph.content) do
      if inline.t == "Math" and inline.mathtype == "DisplayMath" then
        local math_labels = {}
        for label in inline.text:gmatch("\\label%s*{([^{}]+)}") do
          table.insert(math_labels, label)
        end
        if #math_labels > 0 then
          inline.text = inline.text:gsub("\\label%s*{[^{}]+}", "")
          for _, label in ipairs(math_labels) do
            content:insert(pandoc.Span({}, pandoc.Attr(label, { "equation-anchor" })))
          end
          changed = true
        end
      end
      content:insert(inline)
    end
    if changed then
      paragraph.content = content
    end
    return paragraph
  end

  local result = document:walk({
    RawInline = function(inline)
      if inline.format == "latex" and inline.text:match("^%s*\\thefigure%s*$") then
        thefigure_index = thefigure_index + 1
        local value = thefigure_values[thefigure_index]
        if not value then
          error("LaTeX EPUB 无法按顺序解析 \\thefigure")
        end
        return pandoc.Str(value)
      elseif number_captions and inline.format == "latex"
        and inline.text:match("^%s*\\thetable%s*$") then
        thetable_index = thetable_index + 1
        local value = thetable_values[thetable_index]
        if not value then
          error("LaTeX EPUB 无法按顺序解析 \\thetable")
        end
        return pandoc.Str(value)
      end
      return inline
    end,
    Para = anchor_math_labels,
    Plain = anchor_math_labels,
  })
  if thefigure_index ~= #thefigure_values then
    error("LaTeX EPUB 图号顺序不闭合")
  end
  if thetable_index ~= #thetable_values then
    error("LaTeX EPUB 表号顺序不闭合")
  end
  return result
end

local function mark_unlisted_headers(document)
  local targets = {}
  local function declaration(element)
    if element.format ~= "latex" then
      return nil
    end
    local arguments = command_args(element.text, "epubunlisted", 1)
    if not arguments then
      return nil
    end
    local identifier = arguments[1]:match("^%s*(.-)%s*$")
    if identifier == "" then
      error("LaTeX epubunlisted 必须指定真实标题标签")
    end
    targets[identifier] = 0
    return {}
  end
  document = document:walk({
    RawInline = declaration,
    RawBlock = declaration,
  })
  if not next(targets) then
    return document
  end
  document = document:walk({
    Header = function(header)
      if targets[header.identifier] ~= nil then
        if not has_class(header, "unnumbered") then
          error("LaTeX epubunlisted 目标必须是不编号标题：" .. header.identifier)
        end
        targets[header.identifier] = targets[header.identifier] + 1
        if not has_class(header, "unlisted") then
          header.classes:insert("unlisted")
        end
      end
      return header
    end,
  })
  for identifier, count in pairs(targets) do
    if count ~= 1 then
      error("LaTeX epubunlisted 目标必须是唯一真实标题：" .. identifier)
    end
  end
  return document
end

local function code_declaration(element)
  if (element.t ~= "RawBlock" and element.t ~= "RawInline")
    or element.format ~= "latex" then
    return false
  end
  local value = element.text:gsub("%s+", ""):gsub("{}$", "")
  return value == "\\epubcodeblock"
end

local function code_spacing(inline)
  if inline.t ~= "RawInline" or inline.format ~= "latex" then
    return inline
  end
  local spaces = 0
  local remaining = inline.text:gsub("\\qquad", function()
    spaces = spaces + 4
    return ""
  end):gsub("\\quad", function()
    spaces = spaces + 2
    return ""
  end)
  if spaces > 0 and not remaining:match("%S") then
    return pandoc.Code(string.rep(" ", spaces))
  end
  if inline.text:match("^%s*\\hfill%s*$") then
    return pandoc.Code("  ")
  end
  return inline
end

local function raw_anchor(inline)
  return inline.t == "RawInline" and inline.format == "latex"
    and (source_markers(inline.text, false)
      or command_args(inline.text, "label", 1)
      or layout_only(inline.text))
end

local function normalize_code_examples(document)
  return document:walk({
    BlockQuote = function(block)
      if #block.content == 0 then
        return nil
      end
      local body_start = 2
      local first = block.content[1]
      if not code_declaration(first) then
        if first.t ~= "Para" and first.t ~= "Plain" then
          return nil
        end
        local index = 1
        while first.content[index] and
          (first.content[index].t == "Space" or first.content[index].t == "SoftBreak") do
          index = index + 1
        end
        if not first.content[index] or not code_declaration(first.content[index]) then
          return nil
        end
        for _ = 1, index do
          first.content:remove(1)
        end
        body_start = 1
      end
      local lines = {}
      local pending = pandoc.Inlines({})
      local function add_line(line, explicit_break)
        local visible = false
        for _, inline in ipairs(line) do
          if inline.t ~= "Space" and inline.t ~= "SoftBreak"
            and not raw_anchor(inline) then
            visible = true
          end
        end
        if not visible and not explicit_break then
          pending:extend(line)
          return
        end
        local content = pandoc.Inlines({})
        content:extend(pending)
        content:extend(line)
        pending = pandoc.Inlines({})
        lines[#lines + 1] = content
      end
      for index = body_start, #block.content do
        local child = block.content[index]
        if child.t == "Para" or child.t == "Plain" then
          local line = pandoc.Inlines({})
          for _, inline in ipairs(child.content) do
            if inline.t == "LineBreak" then
              add_line(line, true)
              line = pandoc.Inlines({})
            else
              line:insert(code_spacing(inline))
            end
          end
          add_line(line, false)
        elseif child.t == "RawBlock" and child.format == "latex" then
          if code_declaration(child) then
            error("LaTeX epubcodeblock 只能声明一次")
          elseif source_markers(child.text, false)
            or command_args(child.text, "label", 1) then
            pending:insert(pandoc.RawInline("latex", child.text))
          elseif not layout_only(child.text) then
            error("LaTeX epubcodeblock 不支持该 raw 块：" .. child.text)
          end
        else
          error("LaTeX epubcodeblock 仅支持原生富文本行和来源锚点")
        end
      end
      if #lines == 0 then
        error("LaTeX epubcodeblock 必须包含实际富文本行")
      end
      lines[#lines]:extend(pending)
      return pandoc.Div({ pandoc.LineBlock(lines) }, pandoc.Attr("", { "code-example" }))
    end,
  })
end

local function plain_anchor_paragraph(paragraph)
  local found = false
  for _, inline in ipairs(paragraph.content) do
    if inline.t == "Span" and inline.identifier ~= "" and #inline.content == 0 then
      found = true
    elseif inline.t == "RawInline" and inline.format == "html" then
      local remaining, count = inline.text:gsub(
        '<span class="source%-page" data%-page="%d+"></span>', ""
      )
      if count == 0 or remaining:match("%S") then
        return nil
      end
      found = true
    elseif inline.t ~= "Space" and inline.t ~= "SoftBreak" then
      return nil
    end
  end
  if found then
    return pandoc.Plain(paragraph.content)
  end
end

function Pandoc(document)
  document = normalize_table_scroll(document)
  document = normalize_code_examples(document)
  document = mark_unlisted_headers(document)
  document = collect_labels(document)
  document = document:walk({
    Image = fallback_image_alt,
    Math = function(element)
      element.text = element.text:gsub("\\sourcepage%s*{%s*%d+%s*}", "")
      element.text = element.text:gsub("\\scriptsize%s*", "")
      element.text = element.text:gsub(
        "\\cnconcept%s*{([^{}]*)}%s*{([^{}]*)}",
        function(target, source)
          return target .. "（\\textit{" .. source .. "}）"
        end
      )
      local normalized = normalized_math_layout(element.text)
      if normalized then
        element.text = normalized
      end
      return element
    end,
    RawInline = function(element)
      return semantic_raw(element, false)
    end,
    RawBlock = function(element)
      return semantic_raw(element, true)
    end,
  })
  document = document:walk({ Para = plain_anchor_paragraph })
  if next(unresolved_refs) then
    local values = {}
    for value in pairs(unresolved_refs) do
      table.insert(values, value)
    end
    table.sort(values)
    error("LaTeX EPUB 存在未解析交叉引用：" .. table.concat(values, ", "))
  end
  if next(unknown_raw) then
    local values = {}
    for value in pairs(unknown_raw) do
      table.insert(values, value:gsub("%s+", " "):sub(1, 160))
    end
    table.sort(values)
    error("LaTeX EPUB 存在未映射 raw TeX：" .. table.concat(values, " | "))
  end
  return document
end
