#let source-page(number) = metadata("source-page:" + str(number))

#let cn-concept(translation, original) = [
  #translation（#emph[#original]）
]

#let verse-block(body, width: 76mm) = block(breakable: false)[
  #grid(
    columns: (1fr, width, 1fr),
    [],
    align(left, body),
    [],
  )
]
