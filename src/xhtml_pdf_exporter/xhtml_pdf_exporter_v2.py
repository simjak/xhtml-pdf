"""
XHTML to PDF converter that determines optimal page orientation using content dimensions.
"""

import enum
import logging
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

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


def _get_pf_orientation(page) -> Optional[str]:
    """
    Determine orientation from .pf page containers, if present.
    Returns 'portrait', 'landscape', or None if no pf elements were found.
    """
    pf_data = page.evaluate(
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


def get_orientation(page) -> str:
    """
    Determine page orientation by first checking .pf elements, then falling
    back to overall content dimensions if no pf elements are found.
    """
    page.set_viewport_size({"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]})
    page.wait_for_selector("body", state="attached")

    # 1. Check .pf elements
    pf_orientation = _get_pf_orientation(page)
    if pf_orientation:
        logger.info(f"Orientation from .pf elements: {pf_orientation}")
        return pf_orientation

    # 2. Fallback to content dimensions
    dims = page.evaluate("""() => {
        // Try elements with page in class name first
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
    }""")

    width, height = dims["width"], dims["height"]
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


class DocumentExporter:
    """Generate a PDF (or JPEG) from XHTML with determined orientation."""

    def __init__(self, input_file: str, export_format: ExportFormat = ExportFormat.PDF):
        self.input_file = Path(input_file)
        self.export_format = export_format
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

    def export(self, output_path: str, jpeg_quality: Optional[int] = None) -> None:
        """Load the page, determine orientation, and export."""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            try:
                page.goto(f"file://{self.input_file.absolute()}")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_load_state("networkidle")

                orientation = get_orientation(page)
                logger.info(f"Determined orientation: {orientation}")

                is_landscape = orientation == "landscape"
                viewport_size = A4_LANDSCAPE if is_landscape else A4_PORTRAIT
                page.set_viewport_size(
                    {"width": viewport_size[0], "height": viewport_size[1]}
                )

                if self.export_format == ExportFormat.PDF:
                    page.pdf(
                        path=output_path,
                        format="A4",
                        landscape=is_landscape,
                        scale=0.98,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                        print_background=True,
                        prefer_css_page_size=False,
                    )
                else:
                    output_dir = Path(output_path).resolve()
                    output_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_options = {
                        "type": "jpeg",
                        "path": str(output_dir / "page_001.jpg"),
                        "full_page": True,
                    }
                    if jpeg_quality is not None:
                        screenshot_options["quality"] = jpeg_quality
                    page.screenshot(**screenshot_options)

                logger.info(
                    f"Export completed successfully ({self.export_format.value.upper()})."
                )
            finally:
                page.close()
                browser.close()


def main():
    """CLI entry point."""
    import argparse

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
    args = parser.parse_args()

    fmt = ExportFormat.PDF if args.format.lower() == "pdf" else ExportFormat.JPEG
    exporter = DocumentExporter(args.input_file, fmt)
    exporter.export(args.output_path, jpeg_quality=args.quality)


if __name__ == "__main__":
    main()
