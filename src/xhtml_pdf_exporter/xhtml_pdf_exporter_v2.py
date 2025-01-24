"""XHTML to PDF converter with automatic orientation detection based on rendered dimensions."""

import enum
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright


class ExportFormat(enum.Enum):
    """Export format options."""

    PDF = "pdf"
    JPEG = "jpeg"

    def __str__(self) -> str:
        return self.value


class DocumentExporter:
    """Export XHTML documents to PDF/JPEG with automatic orientation detection."""

    def __init__(self, input_file: str, export_format: ExportFormat = ExportFormat.PDF):
        """Initialize document exporter with input XHTML file path and format.

        Args:
            input_file: Path to input XHTML file
            export_format: Output format (PDF or JPEG)
        """
        self.input_file = Path(input_file)
        self.export_format = export_format
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

    def detect_orientation(self, page) -> bool:
        """Detect if content is landscape by checking rendered dimensions.

        Args:
            page: Playwright page object

        Returns:
            True if landscape, False if portrait
        """
        # Get full document dimensions as rendered
        width = page.evaluate("document.documentElement.scrollWidth")
        height = page.evaluate("document.documentElement.scrollHeight")

        # Simple width > height comparison
        return width > height

    def export(self, output_path: str, jpeg_quality: Optional[int] = None) -> None:
        """Export XHTML to PDF or JPEG with automatic orientation detection.

        Args:
            output_path: For PDF, this is the output file path. For JPEG, this is the output directory.
            jpeg_quality: Optional quality setting for JPEG export (1-100).
        """
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            try:
                # Load file and wait for render
                print(f"Loading file: {self.input_file.absolute()}")
                try:
                    response = page.goto(f"file://{self.input_file.absolute()}")
                    if response is None:
                        raise Exception("Failed to load page: No response")
                    if not response.ok:
                        raise Exception(f"Failed to load page: {response.status}")
                except Exception as e:
                    raise Exception(f"Failed to load page: {str(e)}")

                print("Waiting for content to load...")
                page.wait_for_load_state("networkidle")
                page.wait_for_load_state("domcontentloaded")

                # Detect orientation
                print("Detecting orientation...")
                is_landscape = self.detect_orientation(page)
                print(f"Detected orientation: {'landscape' if is_landscape else 'portrait'}")

                # Export based on format
                print(f"Exporting to {self.export_format}...")
                if self.export_format == ExportFormat.PDF:
                    page.pdf(
                        path=output_path,
                        print_background=True,
                        prefer_css_page_size=True,
                        landscape=is_landscape
                    )
                else:  # JPEG
                    output_dir = Path(output_path)
                    output_dir.mkdir(parents=True, exist_ok=True)

                    screenshot_options = {
                        "type": "jpeg",
                        "path": str(output_dir / "page_001.jpg"),
                        "full_page": True
                    }
                    if jpeg_quality is not None:
                        screenshot_options["quality"] = jpeg_quality

                    page.screenshot(**screenshot_options)

            except Exception as e:
                raise Exception(f"Export failed: {str(e)}")
            finally:
                browser.close()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Export XHTML to PDF/JPEG with automatic orientation detection"
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
