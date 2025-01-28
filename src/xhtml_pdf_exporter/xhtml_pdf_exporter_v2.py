"""
XHTML to PDF converter that determines optimal page orientation using content dimensions
and prints in batches to reduce memory usage, then merges partial PDFs into one.
Automatically stops when it reaches an empty batch, so you don't need to know how many pages exist.
"""

import argparse
import asyncio
import enum
import logging
import os
from pathlib import Path
from typing import List, Optional

# For merging PDFs
# pip install PyPDF2
import PyPDF2
from playwright.async_api import async_playwright

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ExportFormat(enum.Enum):
    PDF = "pdf"
    JPEG = "jpeg"

    def __str__(self) -> str:
        return self.value


# A4 dimensions
A4_WIDTH_PX = 794  # Base width in pixels
A4_RATIO = 1.414  # Standard A4 ratio (297mm / 210mm)
A4_PORTRAIT = (A4_WIDTH_PX, int(A4_WIDTH_PX * A4_RATIO))  # 794 x 1123
A4_LANDSCAPE = (int(A4_WIDTH_PX * A4_RATIO), A4_WIDTH_PX)  # 1123 x 794


async def _get_pf_orientation_async(page) -> Optional[str]:
    """
    Determine orientation from .pf page containers, if present.
    Returns 'portrait', 'landscape', or None if no pf elements were found.
    """
    pf_data = await page.evaluate(
        """() => {
            const pfElements = Array.from(document.querySelectorAll('.pf'));
            if (!pfElements.length) return null;
            return pfElements.map(el => {
                const rect = el.getBoundingClientRect();
                return { width: rect.width, height: rect.height };
            });
        }"""
    )

    if not pf_data:
        return None

    portrait_count = sum(1 for dims in pf_data if dims["height"] > dims["width"])
    landscape_count = len(pf_data) - portrait_count

    if landscape_count > portrait_count:
        return "landscape"
    return "portrait"


async def get_orientation_async(page) -> str:
    """
    Determine page orientation by first checking .pf elements, then falling
    back to overall content dimensions if no pf elements are found.
    """
    page.set_default_timeout(60000)  # 60 seconds for selectors
    page.set_default_navigation_timeout(60000)

    await page.set_viewport_size({"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]})
    await page.wait_for_selector("body", state="attached")

    # 1. Check .pf elements
    pf_orientation = await _get_pf_orientation_async(page)
    if pf_orientation:
        logger.info(f"Orientation from .pf elements: {pf_orientation}")
        return pf_orientation

    # 2. Fallback to content dimensions
    dims = await page.evaluate(
        """() => {
            // Try elements with 'page' in class name first
            const pageElements = document.querySelectorAll('[class*="page"]');
            if (pageElements.length) {
                const rects = Array.from(pageElements).map(el => el.getBoundingClientRect());
                const dims = {
                    width: Math.max(...rects.map(r => r.width)),
                    height: Math.max(...rects.map(r => r.height))
                };
                if (dims.width >= 400 && dims.height >= 600) {
                    return dims;
                }
            }

            // Fallback to document dimensions
            const doc = document.documentElement;
            return {
                width: Math.max(doc.scrollWidth, doc.clientWidth),
                height: Math.max(doc.scrollHeight, doc.clientHeight)
            };
        }"""
    )

    width = dims["width"]
    height = dims["height"]
    logger.debug(f"Content dimensions: {width}x{height}")

    if height == 0:
        logger.info("Height is 0, defaulting to portrait orientation")
        return "portrait"

    # For reasonable page dimensions, use width/height comparison
    if width >= 400 and height >= 600:
        if height > width:
            logger.info("Content dimensions indicate portrait orientation")
            return "portrait"
        logger.info("Content dimensions indicate landscape orientation")
        return "landscape"

    # For unreasonable dimensions, assume portrait
    logger.info("Unreasonable dimensions, defaulting to portrait orientation")
    return "portrait"


def merge_pdfs(output_path: str, pdf_paths: List[str]) -> None:
    """
    Merge multiple PDF files (in order) into a single PDF at output_path.
    """
    logger.info(f"Merging {len(pdf_paths)} partial PDFs into {output_path}")
    merger = PyPDF2.PdfMerger()
    for p in pdf_paths:
        merger.append(p)
    with open(output_path, "wb") as f_out:
        merger.write(f_out)
    merger.close()

    logger.info("Cleaning up partial PDFs.")
    for p in pdf_paths:
        try:
            os.remove(p)
        except OSError:
            pass


def generate_batch_ranges(
    start_page: int, max_pages: int, batch_size: int
) -> List[str]:
    """
    Generate page range strings in the format "X-Y" for printing in batches.
    Example: start=1, max_pages=120 => ["1-20", "21-40", ..., "101-120"] if batch_size=20

    We'll use max_pages as an upper bound guess. We break early once we detect
    a near-empty file.
    """
    ranges = []
    current = start_page
    while current <= max_pages:
        end_pg = current + batch_size - 1
        if end_pg > max_pages:
            end_pg = max_pages
        ranges.append(f"{current}-{end_pg}")
        current += batch_size
    return ranges


class DocumentExporterAsync:
    """Generate a PDF (or JPEG) from XHTML with determined orientation, using async Playwright."""

    def __init__(self, input_file: str, export_format: ExportFormat = ExportFormat.PDF):
        self.input_file = Path(input_file)
        self.export_format = export_format
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

    async def export(
        self,
        output_path: str,
        jpeg_quality: Optional[int] = None,
        max_pages_guess: int = 500,
        batch_size: int = 10,
    ) -> None:
        """
        Load the page, determine orientation, and export (async).
        Batches are printed with a new Page each time (reducing memory usage).
        We stop early if a batch is empty, meaning no more pages to print.
        """
        chromium_launch_args = [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-background-timer-throttling",
            "--disable-breakpad",
            "--disable-crash-reporter",
        ]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=chromium_launch_args)
            try:
                # Open an initial page to determine orientation, then close it.
                # We'll re-open a fresh page for each batch if PDF.
                orientation_page = await browser.new_page()
                orientation_page.set_default_timeout(120000)
                orientation_page.set_default_navigation_timeout(120000)
                await orientation_page.goto(
                    f"file://{self.input_file.absolute()}",
                    timeout=120000,
                    wait_until="domcontentloaded",
                )

                orientation = await get_orientation_async(orientation_page)
                await orientation_page.close()

                logger.info(f"Determined orientation: {orientation}")
                is_landscape = orientation == "landscape"

                if self.export_format == ExportFormat.JPEG:
                    # Just do a one-shot screenshot
                    page_jpeg = await browser.new_page()
                    if is_landscape:
                        await page_jpeg.set_viewport_size(
                            {"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]}
                        )
                    else:
                        await page_jpeg.set_viewport_size(
                            {"width": A4_PORTRAIT[0], "height": A4_PORTRAIT[1]}
                        )
                    await page_jpeg.goto(
                        f"file://{self.input_file.absolute()}",
                        timeout=120000,
                        wait_until="domcontentloaded",
                    )
                    output_dir = Path(output_path).resolve()
                    output_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_options = {
                        "type": "jpeg",
                        "path": str(output_dir / "page_001.jpg"),
                        "full_page": True,
                    }
                    if jpeg_quality is not None:
                        screenshot_options["quality"] = jpeg_quality
                    await page_jpeg.screenshot(**screenshot_options)
                    await page_jpeg.close()
                    logger.info("Export completed successfully (JPEG).")
                    return

                # If PDF, do batch printing using fresh pages each time.
                partial_files = []
                for page_range in generate_batch_ranges(1, max_pages_guess, batch_size):
                    partial_pdf = f"{output_path}.part_{page_range.replace('-', '_')}"
                    logger.info(f"Printing PDF for pages {page_range} -> {partial_pdf}")

                    # Open a new page for just this batch
                    page_pdf = await browser.new_page()
                    page_pdf.set_default_timeout(120000)
                    page_pdf.set_default_navigation_timeout(120000)

                    # Set accordingly
                    if is_landscape:
                        await page_pdf.set_viewport_size(
                            {"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]}
                        )
                    else:
                        await page_pdf.set_viewport_size(
                            {"width": A4_PORTRAIT[0], "height": A4_PORTRAIT[1]}
                        )

                    # Tolerate slow loads
                    await page_pdf.goto(
                        f"file://{self.input_file.absolute()}",
                        timeout=120000,
                        wait_until="domcontentloaded",
                    )

                    # Now print only the specified batch range
                    try:
                        await page_pdf.pdf(
                            path=partial_pdf,
                            format="A4",
                            landscape=is_landscape,
                            scale=0.98,
                            margin={
                                "top": "0",
                                "right": "0",
                                "bottom": "0",
                                "left": "0",
                            },
                            print_background=True,
                            prefer_css_page_size=False,
                            page_ranges=page_range,
                        )
                    except Exception as exc:
                        logger.error(f"Batch {page_range} failed: {exc}")
                        # Important to still close the page
                        await page_pdf.close()
                        raise

                    await page_pdf.close()

                    if (
                        not os.path.exists(partial_pdf)
                        or os.path.getsize(partial_pdf) < 1024
                    ):
                        logger.info(
                            f"Batch {page_range} produced minimal or no output; assuming end of document."
                        )
                        try:
                            os.remove(partial_pdf)
                        except OSError:
                            pass
                        break

                    partial_files.append(partial_pdf)

                if partial_files:
                    merge_pdfs(output_path, partial_files)
                    logger.info(
                        f"Export completed (PDF) with {len(partial_files)} batch(es)."
                    )
                else:
                    logger.info("No PDF output was generated at all.")

            except Exception as exc:
                logger.error(f"Export failed with error: {exc}", exc_info=True)
                raise
            finally:
                # Ensure the entire browser is closed
                await browser.close()


async def main_async() -> None:
    """CLI entry point for async version."""
    parser = argparse.ArgumentParser()
    parser.add_argument("input_file", help="Input XHTML file path")
    parser.add_argument("output_path", help="Output PDF path or directory (for JPEGs)")
    parser.add_argument(
        "--format",
        "-f",
        default="pdf",
        choices=["pdf", "jpeg"],
        help="Output format: pdf or jpeg",
    )
    parser.add_argument(
        "--quality",
        "-q",
        type=int,
        choices=range(1, 101),
        metavar="[1-100]",
        help="JPEG quality if --format=jpeg",
    )
    parser.add_argument(
        "--max-pages-guess",
        type=int,
        default=50,
        help="Maximum number of pages to try printing in total.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Number of pages per batch (only used for PDF).",
    )
    args = parser.parse_args()

    fmt = ExportFormat.PDF if args.format.lower() == "pdf" else ExportFormat.JPEG
    exporter = DocumentExporterAsync(args.input_file, fmt)
    await exporter.export(
        output_path=args.output_path,
        jpeg_quality=args.quality,
        max_pages_guess=args.max_pages_guess,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    asyncio.run(main_async())
