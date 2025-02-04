import logging
from pathlib import Path
from typing import Dict, List, Union

from playwright.sync_api import Playwright, sync_playwright

from xhtml_pdf_exporter.xhtml_analyzer import analyze_xhtml

logger = logging.getLogger(__name__)


class PageScreenshotter:
    """Takes screenshots of XHTML/iXBRL pages with accurate dimension handling."""

    def __init__(self, xhtml_path: Union[str, Path]):
        self.xhtml_path = Path(xhtml_path)
        self.page_info = self._analyze_document()
        self.browser = None
        self.context = None
        self.page = None

    def _analyze_document(self) -> Dict:
        """Analyze XHTML document to get accurate page information."""
        try:
            return analyze_xhtml(self.xhtml_path)
        except Exception as e:
            logger.error(f"Failed to analyze document: {e}")
            raise

    def _setup_browser(
        self, playwright: Playwright, viewport_size: Dict[str, int]
    ) -> None:
        """Initialize browser with appropriate settings."""
        self.browser = playwright.chromium.launch(
            args=["--disable-dev-shm-usage"]  # Helps with memory issues
        )
        self.context = self.browser.new_context(
            viewport={
                "width": viewport_size["width"],
                "height": viewport_size["height"],
            },
            device_scale_factor=2,  # Higher resolution screenshots
        )
        self.page = self.context.new_page()

    def _get_max_dimensions(self) -> Dict[str, int]:
        """Calculate maximum dimensions needed for viewport."""
        max_width = 0
        max_height = 0

        for page in self.page_info["pages"]:
            dims = page["dimensions"]
            # Convert dimensions to pixels
            width = self._convert_to_pixels(dims["width"])
            height = self._convert_to_pixels(dims["height"])
            max_width = max(max_width, width)
            max_height = max(max_height, height)

        return {
            "width": int(max_width + 100),  # Add padding
            "height": int(max_height + 100),
        }

    def _convert_to_pixels(self, dimension: str) -> float:
        """Convert dimension string to pixels."""
        value = float(dimension.split(".")[0])  # Remove decimal part
        unit = dimension.split("pt")[0][-2:]  # Get unit (pt, px, etc.)

        # Convert common units to pixels
        if unit == "pt":
            return value * 1.3333  # 1pt ≈ 1.3333px at 96 DPI
        elif unit == "px":
            return value
        elif unit == "mm":
            return value * 3.7795  # 1mm ≈ 3.7795px at 96 DPI
        elif unit == "cm":
            return value * 37.795  # 1cm ≈ 37.795px at 96 DPI
        elif unit == "in":
            return value * 96  # 1in = 96px at 96 DPI
        return value

    def _setup_page_view(self, page_info: Dict) -> None:
        """Configure page view for screenshot."""
        dims = page_info["dimensions"]
        width = self._convert_to_pixels(dims["width"])
        height = self._convert_to_pixels(dims["height"])

        # Set viewport and content size
        self.page.set_viewport_size(
            {"width": int(width + 50), "height": int(height + 50)}
        )

        # Inject CSS to ensure proper page rendering
        self.page.add_style_tag(
            content=f"""
            body {{
                margin: 0;
                padding: 0;
                background: white;
            }}
            .pf, .pc, [class*='page'] {{
                width: {dims["width"]} !important;
                height: {dims["height"]} !important;
                margin: 0 auto !important;
                position: relative !important;
                overflow: hidden !important;
            }}
        """
        )

    def take_screenshots(self, output_dir: Union[str, Path]) -> List[Path]:
        """Take screenshots of all pages."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot_paths = []

        with sync_playwright() as playwright:
            viewport_size = self._get_max_dimensions()
            self._setup_browser(playwright, viewport_size)

            try:
                # Navigate to file
                self.page.goto(f"file://{self.xhtml_path.absolute()}")
                self.page.wait_for_load_state("networkidle")

                # Process each page
                for page_info in self.page_info["pages"]:
                    page_num = page_info["number"]
                    output_path = output_dir / f"page_{page_num:03d}.png"

                    try:
                        self._setup_page_view(page_info)

                        # Find the page element
                        selector = ".pf, .pc, [class*='page']"
                        page_element = self.page.locator(selector).nth(page_num - 1)

                        # Take screenshot
                        page_element.screenshot(
                            path=str(output_path),
                            type="png",
                            quality=100,
                            scale="device",
                        )

                        screenshot_paths.append(output_path)
                        logger.info(f"Captured page {page_num}")

                    except Exception as e:
                        logger.error(f"Failed to capture page {page_num}: {e}")
                        continue

            finally:
                self.browser.close()

        return screenshot_paths


def capture_document_pages(
    xhtml_path: Union[str, Path], output_dir: Union[str, Path]
) -> List[Path]:
    """Convenience function to capture all pages from a document."""
    screenshotter = PageScreenshotter(xhtml_path)
    return screenshotter.take_screenshots(output_dir)


if __name__ == "__main__":
    sample_files = [
        "assets/xhtml/sample_0.xhtml",
    ]

    for file in sample_files:
        capture_document_pages(file, f"assets/screenshots/{file.split('/')[-1]}")
