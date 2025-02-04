import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set, Tuple, Union

from playwright.async_api import Browser, async_playwright

from xhtml_pdf_exporter.xhtml_analyzer import XHTMLAnalyzer


@dataclass
class PageDimensions:
    width: float
    height: float
    unit: str

    @staticmethod
    def from_dimension_str(dim_str: str) -> Tuple[float, str]:
        """Parse dimension string like '816.0pt' or '1920.0px'."""
        match = re.match(r"(\d+\.?\d*)(\w+)", dim_str)
        if not match:
            raise ValueError(f"Invalid dimension format: {dim_str}")
        return float(match.group(1)), match.group(2)

    @classmethod
    def from_page_info(cls, page_info: Dict) -> "PageDimensions":
        """Create PageDimensions from page info dictionary."""
        dims = page_info["dimensions"]
        width_val, width_unit = cls.from_dimension_str(dims["width"])
        height_val, height_unit = cls.from_dimension_str(dims["height"])

        if width_unit != height_unit:
            raise ValueError(f"Mismatched units: {width_unit} vs {height_unit}")

        return cls(width=width_val, height=height_val, unit=width_unit)

    def to_pixels(self) -> Tuple[int, int]:
        """Convert dimensions to pixels with proper scaling."""
        dpi_scale = 96.0  # Standard screen DPI

        # Convert to pixels based on unit
        if self.unit == "pt":
            # Use actual scaling factor from document
            scale_factor = 0.75  # 1pt ≈ 0.75px at 96 DPI
            return (int(self.width * scale_factor), int(self.height * scale_factor))
        elif self.unit == "px":
            return int(self.width), int(self.height)
        elif self.unit == "mm":
            mm_to_px = dpi_scale / 25.4  # 25.4 mm per inch
            return int(self.width * mm_to_px), int(self.height * mm_to_px)
        else:
            raise ValueError(f"Unsupported unit: {self.unit}")


def setup_logging(debug: bool = False) -> logging.Logger:
    """Setup logging configuration."""
    logger = logging.getLogger("page_screenshot")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Create handlers
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)

    # Create formatters
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)

    # Add file handler if debug is enabled
    if debug:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_handler = logging.FileHandler(f"screenshot_log_{timestamp}.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.addHandler(console_handler)
    return logger


class PageScreenshotter:
    def __init__(self, debug: bool = False, batch_size: int = 10):
        self.debug = debug
        self.logger = setup_logging(debug)
        self.browser: Optional[Browser] = None
        self.captured_pages: Set[int] = set()
        self.failed_pages: Set[int] = set()
        self.total_pages: int = 0
        self.batch_size = batch_size
        self.max_retries = 3
        self.page_number_map: Dict[int, str] = {}

    async def _create_browser(self) -> Browser:
        """Create a new browser instance with optimized settings."""
        self.logger.info("Creating new browser instance")
        try:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-web-security",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    "--single-process",
                    "--disable-extensions",
                    "--disable-popup-blocking",
                    "--disable-default-apps",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-translate",
                    "--metrics-recording-only",
                    "--mute-audio",
                    "--no-first-run",
                    "--safebrowsing-disable-auto-update",
                    "--js-flags=--max-old-space-size=1024",  # Reduced memory limit
                ],
            )
            self.logger.debug("Browser instance created successfully")
            return browser
        except Exception as e:
            self.logger.error(f"Failed to create browser: {e}")
            raise

    async def _process_batch(
        self,
        browser: Browser,
        batch: list,
        file_url: str,
        output_path: Path,
        batch_start: int,
    ) -> None:
        """Process a batch of pages with error handling and retries."""
        self.logger.info(f"Processing batch starting at page {batch_start}")
        self.logger.debug(f"Batch size: {len(batch)}")

        try:
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
            )
            self.logger.debug("Browser context created")

            for idx, page_info in enumerate(batch, start=batch_start):
                retry_count = 0
                physical_number = idx
                document_number = page_info["content"]["structure"]["numbers"][
                    "document"
                ]

                self.logger.info(
                    f"Processing page {physical_number} (doc #{document_number})"
                )

                while retry_count < self.max_retries:
                    try:
                        # Get page dimensions
                        dimensions = PageDimensions.from_page_info(page_info)
                        width_px, height_px = dimensions.to_pixels()
                        self.logger.debug(
                            f"Page dimensions: {width_px}x{height_px}px "
                            f"(original: {dimensions.width}{dimensions.unit}x"
                            f"{dimensions.height}{dimensions.unit})"
                        )

                        # Create new page with timeout
                        self.logger.debug("Creating new page")
                        page = await asyncio.wait_for(context.new_page(), timeout=30.0)

                        try:
                            # Load page with timeout
                            self.logger.debug(f"Loading URL: {file_url}")
                            await asyncio.wait_for(
                                page.goto(file_url, wait_until="networkidle"),
                                timeout=30.0,
                            )

                            self.logger.debug("Setting viewport size")
                            await page.set_viewport_size(
                                {"width": width_px, "height": height_px}
                            )

                            # Attempt capture
                            self.logger.debug("Attempting page capture")
                            success = await self.capture_element(
                                page, page_info, output_path, physical_number
                            )

                            if success:
                                self.logger.info(
                                    f"Successfully captured page {physical_number}"
                                )
                                break
                            else:
                                self.logger.warning(
                                    f"Failed to capture page {physical_number}"
                                )
                                retry_count += 1

                        except Exception as e:
                            self.logger.error(f"Error during page processing: {str(e)}")
                            raise
                        finally:
                            self.logger.debug("Closing page")
                            await page.close()

                        # Force garbage collection
                        import gc

                        gc.collect()
                        self.logger.debug("Garbage collection performed")

                    except Exception as e:
                        self.logger.error(
                            f"Error processing page {physical_number} "
                            f"(attempt {retry_count + 1}): {e}"
                        )
                        retry_count += 1
                        if retry_count >= self.max_retries:
                            self.logger.error(
                                f"Max retries ({self.max_retries}) reached for "
                                f"page {physical_number}"
                            )
                            self.failed_pages.add(physical_number)
                        await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Batch processing error: {e}")
            raise
        finally:
            self.logger.debug("Closing browser context")
            await context.close()

    async def capture_page_screenshots(
        self, xhtml_path: Union[str, Path], output_dir: Union[str, Path]
    ) -> None:
        """Enhanced screenshot capture with better error handling and recovery."""
        analyzer = XHTMLAnalyzer()
        analyzer.debug = self.debug
        report = analyzer.analyze_file(xhtml_path)
        self.total_pages = report["document_info"]["total_pages"]

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        file_url = f"file://{Path(xhtml_path).absolute()}"

        pages = sorted(report["pages"], key=lambda x: x["number"])
        print(f"Processing {self.total_pages} pages in batches of {self.batch_size}")

        for i in range(0, len(pages), self.batch_size):
            batch = pages[i : i + self.batch_size]
            batch_start = i + 1
            batch_end = min(i + self.batch_size, len(pages))

            print(
                f"\nProcessing batch {i // self.batch_size + 1} "
                f"(Physical pages {batch_start} to {batch_end})"
            )

            # Create new browser for each batch
            browser = await self._create_browser()
            try:
                await self._process_batch(
                    browser, batch, file_url, output_path, batch_start
                )
            finally:
                try:
                    await browser.close()
                except Exception as e:
                    print(f"Error closing browser: {e}")

            # Print progress
            self.print_capture_report()
            await asyncio.sleep(1)  # Brief pause between batches

    def print_capture_report(self) -> None:
        """Enhanced capture report with logging."""
        report_lines = [
            "\nCapture Report:",
            f"Total pages: {self.total_pages}",
            f"Successfully captured: {len(self.captured_pages)}",
            f"Failed to capture: {len(self.failed_pages)}",
        ]

        if self.failed_pages:
            report_lines.append(f"Failed pages: {sorted(list(self.failed_pages))}")

        success_rate = (len(self.captured_pages) / self.total_pages) * 100
        report_lines.append(f"Success rate: {success_rate:.1f}%")

        for line in report_lines:
            self.logger.info(line)


async def main():
    """Example usage"""
    xhtml_path = "assets/xhtml/sample_0.xhtml"
    output_dir = f"assets/screenshots/{Path(xhtml_path).stem}"

    print(f"Capturing screenshots from {xhtml_path} to {output_dir}")

    screenshotter = PageScreenshotter(debug=True)
    await screenshotter.capture_page_screenshots(xhtml_path, output_dir)


if __name__ == "__main__":
    asyncio.run(main())
