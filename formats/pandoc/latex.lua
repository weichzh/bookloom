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

local function latex_inlines(value)
  value = value:gsub("\\greekfont%s*", "")
  local document = pandoc.read(value, "latex+raw_tex")
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
    local content = latex_inlines(concept[1])
    content:insert(pandoc.Space())
    content:insert(pandoc.Str("（"))
    content:insert(pandoc.Emph(latex_inlines(concept[2])))
    content:insert(pandoc.Str("）"))
    local span = pandoc.Span(
      content,
      pandoc.Attr("", { "cn-concept-rendered" }, {
        ["data-original"] = pandoc.utils.stringify(latex_inlines(concept[2])),
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
      local display = target.display
      if command == "eqref" then
        display = "(" .. display .. ")"
      end
      local link = pandoc.Link({ pandoc.Str(display) }, "#" .. target.id)
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

local function collect_labels(document)
  local chapter = 0
  local section = 0
  local subsection = 0
  local figure = 0
  local table_number = 0
  local equation = 0
  local pending_counter
  local counter_formats = {}
  local thefigure_values = {}

  local function scoped(number)
    return chapter > 0 and (chapter .. "." .. number) or tostring(number)
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
        if command == "refstepcounter" then
          pending_counter = formatted_counter(operation[1])
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
        -- Pandoc keeps an entire align environment in one DisplayMath
        -- inline. Such an environment may contain one label per row;
        -- matching only the first label made later rows unresolved.
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
        local custom_display
        if not has_class(block, "unnumbered") then
          if block.level == 1 then
            chapter = chapter + 1
            section = 0
            subsection = 0
            figure = 0
            table_number = 0
            equation = 0
            pending_counter = nil
          elseif block.level == 2 then
            section = section + 1
            subsection = 0
            if counter_formats.section then
              custom_display = formatted_counter("section")
              local content = pandoc.Inlines({
                pandoc.Span(
                  { pandoc.Str(custom_display) },
                  pandoc.Attr("", { "header-section-number" })
                ),
                pandoc.Space(),
              })
              content:extend(block.content)
              block.content = content
              block.classes:insert("unnumbered")
            end
          elseif block.level == 3 then
            subsection = subsection + 1
          end
        end
        if block.identifier ~= "" then
          local display = pandoc.utils.stringify(block.content)
          if custom_display then
            display = custom_display
          elseif not has_class(block, "unnumbered") then
            if block.level == 1 then
              display = tostring(chapter)
            elseif block.level == 2 then
              display = chapter .. "." .. section
            elseif block.level == 3 then
              display = chapter .. "." .. section .. "." .. subsection
            end
          end
          labels[block.identifier] = { id = block.identifier, display = display }
        end
      elseif block.t == "Figure" then
        figure = figure + 1
        local saved_format = counter_formats.figure
        scan_blocks(block.caption.long)
        scan_blocks(block.content)
        if block.identifier ~= "" then
          labels[block.identifier] = {
            id = block.identifier,
            display = formatted_counter("figure"),
          }
        end
        counter_formats.figure = saved_format
      elseif block.t == "Table" then
        table_number = table_number + 1
        if block.identifier ~= "" then
          labels[block.identifier] = {
            id = block.identifier,
            display = formatted_counter("table"),
          }
        end
      elseif block.t == "RawBlock" and block.format == "latex" then
        apply_counter_raw(block.text)
      elseif block.t == "Para" or block.t == "Plain" then
        scan_inlines(block.content)
      elseif block.t == "Div" or block.t == "BlockQuote" then
        scan_blocks(block.content)
      elseif block.t == "BulletList" or block.t == "OrderedList" then
        for _, item in ipairs(block.content) do
          scan_blocks(item)
        end
      end
    end
  end

  scan_blocks(document.blocks)
  local thefigure_index = 0
  local result = document:walk({
    RawInline = function(inline)
      if inline.format == "latex" and inline.text:match("^%s*\\thefigure%s*$") then
        thefigure_index = thefigure_index + 1
        local value = thefigure_values[thefigure_index]
        if not value then
          error("LaTeX EPUB 无法按顺序解析 \\thefigure")
        end
        return pandoc.Str(value)
      end
      return inline
    end,
    Para = function(paragraph)
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
      return changed and pandoc.Para(content) or paragraph
    end,
  })
  if thefigure_index ~= #thefigure_values then
    error("LaTeX EPUB 图号顺序不闭合")
  end
  return result
end

function Pandoc(document)
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
