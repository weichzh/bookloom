package org.allenai.pdffigures2

import java.io.{File, PrintWriter}
import org.apache.pdfbox.pdmodel.PDDocument
import org.allenai.pdffigures2.JsonProtocol._

/** Audit adapter: call the unchanged official API on selected zero-based pages. */
object NativeAuditRunner {
  def main(args: Array[String]): Unit = {
    require(args.length == 4, "input.pdf raw.json timing.json comma-separated-zero-based-pages")
    val pages = args(3).split(",").map(_.toInt).toSeq
    val start = System.nanoTime()
    val doc = PDDocument.load(new File(args(0)))
    val loaded = System.nanoTime()
    try {
      val output = FigureExtractor().getFiguresWithErrors(doc, Some(pages))
      val extracted = System.nanoTime()
      val raw: Map[String, Either[Seq[Figure], Seq[Caption]]] = Map(
        "figures" -> Left(output.figures),
        "regionless-captions" -> Right(output.failedCaptions)
      )
      FigureRenderer.saveAsJSON(args(1), raw)
      val w = new PrintWriter(new File(args(2)), "UTF-8")
      try {
        w.println("{\"pdf_load_seconds\":" + ((loaded-start)/1e9) +
          ",\"figure_extraction_seconds\":" + ((extracted-loaded)/1e9) +
          ",\"figure_count\":" + output.figures.size +
          ",\"regionless_caption_count\":" + output.failedCaptions.size + "}")
      } finally { w.close() }
    } finally { doc.close() }
  }
}
