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

import PyPDF2
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.DEBUG)
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


async def get_content_dimensions_async(page) -> dict:
    """
    Get accurate content dimensions by checking multiple methods.
    Returns a dict with width and height.
    """
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

            // Try .pf elements (PDF containers)
            const pfElements = document.querySelectorAll('.pf');
            if (pfElements.length) {
                const rects = Array.from(pfElements).map(el => el.getBoundingClientRect());
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
            const body = document.body;
            return {
                width: Math.max(
                    doc.scrollWidth,
                    doc.clientWidth,
                    body ? body.scrollWidth : 0,
                    body ? body.clientWidth : 0
                ),
                height: Math.max(
                    doc.scrollHeight,
                    doc.clientHeight,
                    body ? body.scrollHeight : 0,
                    body ? body.clientHeight : 0
                )
            };
        }"""
    )

    width = dims["width"]
    height = dims["height"]

    # Log detailed dimension information
    logger.debug(f"[Dimensions] Raw dimensions: {width}x{height}")
    logger.debug(f"[Dimensions] Width/Height ratio: {width / height:.4f}")
    logger.debug(f"[Dimensions] Aspect ratio (height/width): {height / width:.4f}")

    return dims

async def get_orientation_async(page) -> str:
    """
    Determine page orientation by first checking .pf elements, then falling
    back to overall content dimensions if no pf elements are found.
    """
    page.set_default_timeout(60000)  # 60 seconds for selectors
    page.set_default_navigation_timeout(60000)

    # await page.set_viewport_size({"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]})
    await page.wait_for_selector("body", state="attached")

    # 1. Check .pf elements
    pf_orientation = await _get_pf_orientation_async(page)
    if pf_orientation:
        logger.info(f"Orientation from .pf elements: {pf_orientation}")
        return pf_orientation

    # 2. Get content dimensions
    dims = await get_content_dimensions_async(page)
    width = dims["width"]
    height = dims["height"]

    if height == 0:
        logger.debug("[Dimensions] Zero height detected")
        logger.info("Height is 0, defaulting to portrait orientation")
        return "portrait"

    # For reasonable page dimensions, use width/height comparison
    if width >= 400 and height >= 600:
        logger.debug(
            f"[Dimensions] Meets minimum size requirements: width >= 400 ({width >= 400}), height >= 600 ({height >= 600})"
        )

        # Check for very wide and tall content
        if width > 1000 and height > 100000:
            logger.debug(
                f"[Dimensions] Large content detected: width > 1000 ({width > 1000}), height > 100000 ({height > 100000})"
            )
            logger.info(
                "Content dimensions indicate landscape orientation (large content)"
            )
            return "landscape"

        if height > width:
            logger.debug(f"[Dimensions] Height > Width comparison: {height} > {width}")
            logger.info("Content dimensions indicate portrait orientation")
            return "portrait"

        logger.debug(f"[Dimensions] Width >= Height comparison: {width} >= {height}")
        logger.info("Content dimensions indicate landscape orientation")
        return "landscape"

    # For unreasonable dimensions, assume portrait
    logger.debug(
        f"[Dimensions] Unreasonable dimensions: width < 400 ({width < 400}) or height < 600 ({height < 600})"
    )
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
        max_pages_guess: int = 500,
        batch_size: int = 10,
        split_threshold_size_mb: int = 50,
        timeout_seconds: int = 300,
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

        input_filesize_bytes = self.input_file.stat().st_size
        threshold_bytes = split_threshold_size_mb * 1024 * 1024
        logger.info(f"Input file size: {input_filesize_bytes / 1024 / 1024:.2f} MB")
        logger.info(f"Split threshold: {threshold_bytes / 1024 / 1024:.2f} MB")
        do_batch_print = input_filesize_bytes > threshold_bytes
        logger.info(f"Do batch print: {do_batch_print}")

        # Convert the given timeout in seconds to milliseconds
        nav_timeout_ms = timeout_seconds * 1000

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=chromium_launch_args)
            try:
                # Open an initial page to determine orientation, then close it.
                # We'll re-open a fresh page for each batch if PDF.
                orientation_page = await browser.new_page()
                orientation_page.set_default_timeout(nav_timeout_ms)
                orientation_page.set_default_navigation_timeout(nav_timeout_ms)
                await orientation_page.goto(
                    f"file://{self.input_file.absolute()}",
                    timeout=nav_timeout_ms,
                    wait_until="domcontentloaded",
                )

                orientation = await get_orientation_async(orientation_page)
                await orientation_page.close()

                logger.info(f"Determined orientation: {orientation}")
                is_landscape = orientation == "landscape"

                # If PDF, either do a single print or batch printing based on file size
                if not do_batch_print:
                    # Single print for smaller files
                    page_pdf = await browser.new_page()
                    page_pdf.set_default_timeout(nav_timeout_ms)
                    page_pdf.set_default_navigation_timeout(nav_timeout_ms)

                    # Set viewport size based on orientation
                    if is_landscape:
                        await page_pdf.set_viewport_size(
                            {"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]}
                        )
                    else:
                        await page_pdf.set_viewport_size(
                            {"width": A4_PORTRAIT[0], "height": A4_PORTRAIT[1]}
                        )

                    # Load the page
                    await page_pdf.goto(
                        f"file://{self.input_file.absolute()}",
                        timeout=nav_timeout_ms,
                        wait_until="domcontentloaded",
                    )

                    # Get content dimensions for scaling
                    dims = await get_content_dimensions_async(page_pdf)
                    content_width = dims["width"]
                    target_width = A4_LANDSCAPE[0] if is_landscape else A4_PORTRAIT[0]
                    scale_factor = min(target_width / content_width, 1.0)  # Never scale up
                    logger.info(f"Using scale factor: {scale_factor:.4f} (content width: {content_width}px, target width: {target_width}px)")

                    # Print entire document at once
                    try:
                        await page_pdf.pdf(
                            path=output_path,
                            format="A4",
                            landscape=is_landscape,
                            scale=scale_factor,
                            margin={
                                "top": "10mm",
                                "right": "10mm",
                                "bottom": "10mm",
                                "left": "10mm",
                            },
                            print_background=True,
                            prefer_css_page_size=True,
                        )
                        logger.info("Export completed (PDF) in single print.")
                    except Exception as exc:
                        logger.error(f"PDF generation failed: {exc}")
                        await page_pdf.close()
                        raise

                    await page_pdf.close()
                else:
                    # Batch printing for larger files
                    partial_files = []
                    for page_range in generate_batch_ranges(
                        1, max_pages_guess, batch_size
                    ):
                        partial_pdf = (
                            f"{output_path}.part_{page_range.replace('-', '_')}"
                        )
                        logger.info(
                            f"Printing PDF for pages {page_range} -> {partial_pdf}"
                        )

                        # Open a new page for just this batch
                        page_pdf = await browser.new_page()
                        page_pdf.set_default_timeout(nav_timeout_ms)
                        page_pdf.set_default_navigation_timeout(nav_timeout_ms)

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
                            timeout=nav_timeout_ms,
                            wait_until="domcontentloaded",
                        )

                        # Get content dimensions for scaling
                        dims = await get_content_dimensions_async(page_pdf)
                        content_width = dims["width"]
                        target_width = A4_LANDSCAPE[0] if is_landscape else A4_PORTRAIT[0]
                        scale_factor = min(target_width / content_width, 1.0)  # Never scale up
                        logger.info(f"Using scale factor: {scale_factor:.4f} (content width: {content_width}px, target width: {target_width}px)")

                        # Attempt to print only the specified batch range
                        try:
                            await page_pdf.pdf(
                                path=partial_pdf,
                                format="A4",
                                landscape=is_landscape,
                                scale=scale_factor,
                                margin={
                                    "top": "10mm",
                                    "right": "10mm",
                                    "bottom": "10mm",
                                    "left": "10mm",
                                },
                                print_background=True,
                                prefer_css_page_size=True,
                                page_ranges=page_range,
                            )
                        except Exception as exc:
                            # Gracefully handle "page range exceeds page count" errors
                            if "Page range exceeds page count" in str(exc):
                                logger.info(
                                    f"Batch {page_range} goes beyond the final page. Stopping early."
                                )
                                await page_pdf.close()
                                break
                            else:
                                logger.error(f"Batch {page_range} failed: {exc}")
                                await page_pdf.close()
                                raise

                        await page_pdf.close()

                        # If there's no partial PDF or it's empty, we assume we've reached the end
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
    parser.add_argument("output_path", help="Output PDF path")
    parser.add_argument(
        "--max-pages-guess",
        type=int,
        default=1000,
        help="Maximum number of pages to try printing in total.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of pages per batch (only used for PDF).",
    )
    parser.add_argument(
        "--split-threshold-mb",
        type=int,
        default=50,
        help="If input file size (MB) is larger, use multiple batches. Otherwise, just one batch.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds for loading the page (default 300).",
    )
    args = parser.parse_args()

    exporter = DocumentExporterAsync(args.input_file)
    await exporter.export(
        output_path=args.output_path,
        max_pages_guess=args.max_pages_guess,
        batch_size=args.batch_size,
        split_threshold_size_mb=args.split_threshold_mb,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    asyncio.run(main_async())
