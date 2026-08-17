#let standard-book(body, lang: "en") = {
  let text-lang = if lang == "zh" or lang == "zh-CN" { "zh" } else { lang }
  let indent = if text-lang == "zh" { 2em } else { 1.1em }

  set page(
    width: 152.4mm,
    height: 228.6mm,
    margin: (top: 18mm, bottom: 18mm, left: 17mm, right: 17mm),
  )
  set text(
    font: ("Libertinus Serif", "Source Han Serif SC"),
    size: 10.5pt,
    lang: text-lang,
  )
  set par(
    justify: true,
    first-line-indent: indent,
    leading: 0.55em,
    spacing: 0.82em,
  )
  set heading(numbering: none)
  show emph: it => text(style: "italic")[#it.body]

  body
}
