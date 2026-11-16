-- Pandoc's HTML writer preserves the list's start and style, but not its
-- delimiter. Keep the list native and mark just this supported delimiter.
function OrderedList(element)
  if FORMAT:match("^epub") and element.style == "Decimal"
      and element.delimiter == "TwoParens" then
    return pandoc.Div(
      { element },
      pandoc.Attr("", { "translator-list-decimal-two-parens" }, {})
    )
  end
end
