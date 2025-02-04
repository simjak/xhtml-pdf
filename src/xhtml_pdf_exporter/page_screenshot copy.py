import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple, Union

from playwright.async_api import async_playwright

from xhtml_pdf_exporter.xhtml_analyzer import analyze_xhtml


@dataclass
class PageDimensions:
    width: float
    height: float
    unit: str

    @staticmethod
    def from_dimension_str(dim_str: str) -> Tuple[float, str]:
        """Parse dimension string like '816.0pt' or '1920.0px' into (value, unit)."""
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
        """Convert dimensions to pixels based on unit."""
        if self.unit == "px":
            return int(self.width), int(self.height)
        elif self.unit == "pt":
            # Convert points to pixels (1pt ≈ 1.3333px at 96 DPI)
            return int(self.width * 1.3333), int(self.height * 1.3333)
        else:
            raise ValueError(f"Unsupported unit: {self.unit}")


def build_selector(hierarchy: list[str], page_number: int) -> str:
    """
    Build a CSS selector from container hierarchy, preserving classes.
    Example: ['html', 'body', 'div.pf.w0.h0'] -> 'html > body > div.pf.w0.h0'

    Args:
        hierarchy: List of element selectors
        page_number: Page number to help identify unique elements
    """
    # Get the last element which should be the page container
    if not hierarchy:
        return ""

    # Build the full path but make the last element (page container) more specific
    path = hierarchy[:-1]
    last_element = hierarchy[-1]

    # For debugging
    print(f"Building selector for page {page_number}")
    print(f"Last element: {last_element}")

    # Make last element more specific based on common page container patterns
    if '.pf' in last_element:
        # For elements with pf class, they usually have numbered classes like w0, h0
        last_element = f"{last_element}:nth-of-type({page_number})"
    elif '.pc' in last_element:
        # For pc (page container) elements
        last_element = f"{last_element}:nth-of-type({page_number})"
    elif '.pageView' in last_element:
        # For pageView elements
        last_element = f"{last_element}:nth-of-type({page_number})"
    else:
        # For other elements, add nth-of-type
        last_element = f"{last_element}:nth-of-type({page_number})"

    return " > ".join(path + [last_element])


async def capture_page_screenshots(
    xhtml_path: Union[str, Path], output_dir: Union[str, Path]
) -> None:
    """
    Capture screenshots of each page element in the XHTML file using Playwright.

    Args:
        xhtml_path: Path to the XHTML file
        output_dir: Directory to save the screenshots
    """
    # Analyze XHTML to get page information
    report = analyze_xhtml(xhtml_path)

    # Create output directory if it doesn't exist
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Convert file path to file URL
    file_url = f"file://{Path(xhtml_path).absolute()}"

    async with async_playwright() as p:
        # Launch browser with largest viewport
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 2000, "height": 2000}
        )
        page = await context.new_page()

        # Navigate to the file once
        print(f"Loading file: {file_url}")
        await page.goto(file_url)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_load_state("networkidle")

        # Store captured positions to ensure uniqueness
        captured_positions = set()

        # Process each page from the analyzer
        for page_info in report["pages"]:
            try:
                # Get page dimensions
                dimensions = PageDimensions.from_page_info(page_info)
                width_px, height_px = dimensions.to_pixels()

                # Build precise selector using full hierarchy
                hierarchy = page_info["content"]["container_hierarchy"]
                if not hierarchy:
                    print(f"No hierarchy for page {page_info['number']}, skipping")
                    continue

                selector = build_selector(hierarchy, page_info["number"])
                print(f"Using selector: {selector}")

                try:
                    # Wait for the element to be visible
                    element = await page.wait_for_selector(selector, timeout=5000)
                    if element:
                        # Get element position
                        bbox = await element.bounding_box()
                        if not bbox:
                            print(f"Could not get bounding box for page {page_info['number']}")
                            continue

                        position_key = f"{bbox['x']},{bbox['y']}"
                        if position_key in captured_positions:
                            print(f"Warning: Page {page_info['number']} position {position_key} already captured")
                            continue

                        captured_positions.add(position_key)

                        # Take screenshot with exact dimensions
                        await element.screenshot(
                            path=str(output_path / f"page_{page_info['number']}.png"),
                            type="png",
                            scale="css",  # Use CSS pixels for accurate dimensions
                        )
                        print(
                            f"Captured page {page_info['number']} ({width_px}x{height_px}px) at position {position_key}"
                        )
                except Exception as e:
                    print(f"Error capturing page {page_info['number']}: {e}")
                    print(f"Full hierarchy: {hierarchy}")

                    # Try fallback selector using nth-child
                    try:
                        last_tag = hierarchy[-1].split('.')[0]
                        fallback_selector = f"{last_tag}:nth-child({page_info['number']})"
                        print(f"Trying fallback selector: {fallback_selector}")

                        element = await page.wait_for_selector(fallback_selector, timeout=5000)
                        if element:
                            bbox = await element.bounding_box()
                            if not bbox:
                                print(f"Could not get bounding box for fallback on page {page_info['number']}")
                                continue

                            position_key = f"{bbox['x']},{bbox['y']}"
                            if position_key in captured_positions:
                                print(f"Warning: Fallback page {page_info['number']} position {position_key} already captured")
                                continue

                            captured_positions.add(position_key)

                            await element.screenshot(
                                path=str(output_path / f"page_{page_info['number']}.png"),
                                type="png",
                                scale="css"
                            )
                            print(
                                f"Captured page {page_info['number']} with fallback selector at position {position_key}"
                            )
                    except Exception as e2:
                        print(f"Fallback also failed for page {page_info['number']}: {e2}")

            except Exception as e:
                print(f"Failed to process page {page_info['number']}: {e}")
                continue

        await browser.close()


async def main():
    """Example usage"""
    xhtml_path = "assets/xhtml/sample_0.xhtml"
    output_dir = f"assets/screenshots/{Path(xhtml_path).stem}"

    print(f"Capturing screenshots from {xhtml_path} to {output_dir}")
    await capture_page_screenshots(xhtml_path, output_dir)


if __name__ == "__main__":
    asyncio.run(main())
