import enum
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page, sync_playwright
from PyPDF2 import PdfMerger


class ExportFormat(enum.Enum):
    """Export format options."""

    PDF = "pdf"
    JPEG = "jpeg"

    def __str__(self) -> str:
        return self.value


class DocumentExporter:
    def __init__(self, input_file: str, export_format: ExportFormat = ExportFormat.PDF):
        """Initialize document exporter with input XHTML file path and format."""
        self.input_file = Path(input_file)
        self.export_format = export_format
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

    def export(self, output_path: str, jpeg_quality: Optional[int] = None) -> None:
        """Export XHTML to PDF or JPEG with dynamic page orientation detection.

        Args:
            output_path: For PDF, this is the output file path. For JPEG, this is the output directory.
            jpeg_quality: Optional quality setting for JPEG export (1-100).
        """
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            # Load XHTML file
            page.goto(f"file://{self.input_file.absolute()}")

            # Wait for content to load
            page.wait_for_load_state("networkidle")

            # Get page dimensions
            dimensions = self._get_page_dimensions(page)

            # Apply appropriate CSS based on format
            if self.export_format == ExportFormat.PDF:
                self._inject_orientation_css(page)
                self._apply_orientation_classes(page, dimensions)
                self._export_pdf(page, dimensions, output_path)
            else:  # JPEG
                self._export_jpeg(page, dimensions, output_path, jpeg_quality)

            browser.close()

    def _export_pdf(
        self, page: Page, dimensions: List[Tuple[float, float]], output_file: str
    ) -> None:
        """Export pages to a single PDF file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_files = []

            # Generate PDF for each page with correct orientation
            for i, (width, height) in enumerate(dimensions):
                # Configure PDF options and viewport for this page
                is_landscape = width > height
                pdf_options = self._configure_pdf_options(is_landscape)
                viewport = self._configure_viewport(is_landscape)

                # Set viewport size
                page.set_viewport_size(viewport)

                # Hide all pages except current one
                page.evaluate(
                    """(index) => {
                    document.querySelectorAll('div.pageView').forEach((el, i) => {
                        el.style.display = i === index ? 'block' : 'none';
                    });
                }""",
                    i,
                )

                # Generate PDF for this page
                temp_pdf = os.path.join(temp_dir, f"page_{i}.pdf")
                page.pdf(path=temp_pdf, **pdf_options)
                pdf_files.append(temp_pdf)

            # Merge PDFs
            merger = PdfMerger()
            for pdf_file in pdf_files:
                merger.append(pdf_file)

            merger.write(output_file)
            merger.close()

    def _inject_jpeg_css(self, page: Page) -> None:
        """Inject CSS optimized for JPEG screenshot capture."""
        page.add_style_tag(
            content="""
            @page {
                size: A4 portrait;
                margin: 0;
            }
            @page landscape {
                size: A4 landscape;
                margin: 0;
            }
            div.pageView {
                margin: 0;
                padding: 0;
                position: absolute;
                top: 0;
                left: 0;
            }
            div.pageView.landscape {
                page: landscape;
            }
        """
        )

    def _export_jpeg(
        self,
        page: Page,
        dimensions: List[Tuple[float, float]],
        output_dir: str,
        quality: Optional[int] = None,
    ) -> None:
        """Export each page as a JPEG file with optimized quality."""
        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Generate JPEG for each page with correct orientation
        for i, (width, height) in enumerate(dimensions):
            # Use actual page dimensions for viewport (rounded to integers)
            viewport = {
                "width": round(width),
                "height": round(height)
            }

            # Set viewport and inject optimized CSS
            page.set_viewport_size(viewport)
            self._inject_jpeg_css(page)

            # Hide all pages except current one
            page.evaluate(
                """(index) => {
                document.querySelectorAll('div.pageView').forEach((el, i) => {
                    el.style.display = i === index ? 'block' : 'none';
                });
            }""",
                i,
            )

            # Configure screenshot options for full page capture
            screenshot_options = {
                "type": "jpeg",
                "path": str(output_path / f"page_{i:03d}.jpg"),
                "full_page": False,  # We want viewport size exactly
                "scale": "css",  # Use CSS pixels for accurate sizing
            }
            if quality is not None:
                screenshot_options["quality"] = quality

            # Generate JPEG for this page
            page.screenshot(**screenshot_options)

    def _get_page_dimensions(self, page: Page) -> List[Tuple[float, float]]:
        """Get dimensions for each page section to determine orientation."""
        # Get all page view divs
        dimensions = page.eval_on_selector_all(
            "div.pageView",
            """
            elements => elements.map(el => {
                const rect = el.getBoundingClientRect();
                return [rect.width, rect.height];
            })
        """,
        )
        return dimensions

    def _inject_orientation_css(self, page: Page) -> None:
        """Inject CSS for handling different page orientations."""
        page.add_style_tag(
            content="""
            @page {
                size: A4 portrait;
                margin: 0;
            }
            @page landscape {
                size: A4 landscape;
                margin: 0;
            }
            div.pageView {
                margin-bottom: 20px;
                transform-origin: top left;
                transform: scale(0.95);
            }
            div.pageView.landscape {
                page: landscape;
            }
        """
        )

    def _apply_orientation_classes(
        self, page: Page, dimensions: List[Tuple[float, float]]
    ) -> None:
        """Apply orientation classes to page divs based on dimensions."""
        page.evaluate(
            """(dimensions) => {
            document.querySelectorAll('div.pageView').forEach((el, i) => {
                const [width, height] = dimensions[i];
                if (width > height) {
                    el.classList.add('landscape');
                }
            });
        }""",
            dimensions,
        )

    def _configure_viewport(self, is_landscape: bool) -> Dict[str, int]:
        """Configure viewport size based on orientation."""
        if is_landscape:
            return {
                "width": 1414,  # A4 landscape width
                "height": 1000,  # A4 landscape height
            }
        else:
            return {
                "width": 1000,  # A4 portrait width
                "height": 1414,  # A4 portrait height
            }

    def _configure_pdf_options(self, is_landscape: bool) -> Dict[str, Any]:
        """Configure PDF options based on orientation."""
        return {
            "print_background": True,
            "prefer_css_page_size": True,
            "format": "A4",
            "landscape": is_landscape,
            "scale": 0.95,  # Scale down slightly to ensure content fits
        }


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Export XHTML to PDF or JPEG with dynamic orientation"
    )
    parser.add_argument("input_file", help="Input XHTML file path")
    parser.add_argument(
        "output_path", help="Output PDF file or directory path for JPEGs"
    )
    parser.add_argument(
        "--format",
        "-f",
        type=ExportFormat,
        choices=list(ExportFormat),
        default=ExportFormat.PDF,
        help="Output format (pdf or jpeg)",
    )
    parser.add_argument(
        "--quality",
        "-q",
        type=int,
        choices=range(1, 101),
        metavar="[1-100]",
        help="JPEG quality (1-100, only applies to JPEG format)",
    )

    args = parser.parse_args()

    exporter = DocumentExporter(args.input_file, args.format)
    exporter.export(args.output_path, args.quality)


if __name__ == "__main__":
    main()
