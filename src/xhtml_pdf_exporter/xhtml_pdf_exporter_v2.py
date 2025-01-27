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
    """
    Exports XHTML to PDF/JPEG, deciding orientation by:
      1) Opening with a large viewport (1920×3000) to read natural scrollWidth/scrollHeight.
      2) If scrollWidth ≥ LANDSCAPE_WIDTH_THRESHOLD => candidate for landscape.
      3) Then check if scrollHeight/scrollWidth is very large => override to portrait.
      4) Otherwise choose portrait if under threshold.
    """

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

    def export(self, output_path: str, jpeg_quality: Optional[int] = None) -> None:
        """
        Hybrid approach:
          1) large viewport => measure scrollWidth, scrollHeight
          2) if scrollWidth >= 1100 => potential landscape
          3) but if (scrollHeight/scrollWidth) > 2 => override to portrait
          4) PDF export with format='A4' or screenshot for JPEG
        """
        LANDSCAPE_WIDTH_THRESHOLD = 1100
        EXTREME_TALL_RATIO = 2.0  # if height is > 2× width, force portrait

        with sync_playwright() as p:
            browser = p.chromium.launch()
            detect_page = browser.new_page()
            detect_page.set_viewport_size({"width": 1920, "height": 3000})

            # ------------------- Pass #1: Orientation detection -------------------
            try:
                print(f"Loading file: {self.input_file.absolute()}")
                response = detect_page.goto(f"file://{self.input_file.absolute()}")
                if response is None:
                    raise Exception("Failed to load page: No response")
                if not response.ok:
                    raise Exception(f"Failed to load page: {response.status}")

                detect_page.wait_for_load_state("networkidle")
                detect_page.wait_for_load_state("domcontentloaded")
                detect_page.wait_for_load_state("load")

                scroll_width = detect_page.evaluate(
                    "document.documentElement.scrollWidth"
                )
                scroll_height = detect_page.evaluate(
                    "document.documentElement.scrollHeight"
                )
                print(
                    f"[Initial Detection] scrollWidth={scroll_width}, scrollHeight={scroll_height}"
                )

                if scroll_width >= LANDSCAPE_WIDTH_THRESHOLD:
                    # Candidate for landscape
                    ratio = float(scroll_height) / float(scroll_width)
                    if ratio > EXTREME_TALL_RATIO:
                        # If it's extremely tall, override to portrait
                        is_landscape = False
                        reason = (
                            f"Height/width ratio={ratio:.2f} > {EXTREME_TALL_RATIO}"
                        )
                    else:
                        is_landscape = True
                        reason = (
                            f"Within ratio <= {EXTREME_TALL_RATIO}, so keep landscape"
                        )
                else:
                    # Under threshold => definitely portrait
                    is_landscape = False
                    reason = f"scrollWidth < {LANDSCAPE_WIDTH_THRESHOLD}px"

                orientation_str = "landscape" if is_landscape else "portrait"
                print(
                    f"[Auto-Detection] => Orientation = {orientation_str}, reason: {reason}"
                )

            except Exception as e:
                detect_page.close()
                browser.close()
                raise Exception(f"Export failed during orientation detection: {e}")
            detect_page.close()

            # ------------------- Pass #2: Final export -------------------
            page = browser.new_page()
            try:
                print("Reloading for final export...")
                response = page.goto(f"file://{self.input_file.absolute()}")
                if response is None:
                    raise Exception("Failed to load page for final export: No response")
                if not response.ok:
                    raise Exception(
                        f"Failed to load page for final export: {response.status}"
                    )

                page.wait_for_load_state("networkidle")
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_load_state("load")

                scale_factor = 0.98
                print(f"Using final orientation: {orientation_str}")
                print(f"Using scale factor: {scale_factor:.2f}")

                if self.export_format == ExportFormat.PDF:
                    print(
                        "Exporting as PDF with format='A4' and no custom width/height..."
                    )
                    page.pdf(
                        path=output_path,
                        format="A4",
                        landscape=is_landscape,
                        scale=scale_factor,
                        margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                        print_background=True,
                        prefer_css_page_size=False,
                    )
                else:
                    print("Exporting as JPEG...")
                    from pathlib import Path

                    output_dir = Path(output_path)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_options = {
                        "type": "jpeg",
                        "path": str(output_dir / "page_001.jpg"),
                        "full_page": True,
                    }
                    if jpeg_quality is not None:
                        screenshot_options["quality"] = jpeg_quality
                    page.screenshot(**screenshot_options)

                print("Export completed successfully")

            except Exception as e:
                raise Exception(f"Export failed during final export: {e}")
            finally:
                page.close()
                browser.close()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Export XHTML to PDF/JPEG with orientation auto-chosen by width threshold + tall ratio override. "
            "If scrollWidth >= 1100 => candidate landscape, unless it's extremely tall => force portrait."
        )
    )
    parser.add_argument("input_file", help="Input XHTML path")
    parser.add_argument("output_path", help="Output PDF path or screenshot dir")
    parser.add_argument(
        "--format",
        "-f",
        type=ExportFormat,
        choices=list(ExportFormat),
        default=ExportFormat.PDF,
        help="pdf or jpeg",
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

    exporter = DocumentExporter(args.input_file, args.format)
    exporter.export(args.output_path, jpeg_quality=args.quality)


if __name__ == "__main__":
    main()
