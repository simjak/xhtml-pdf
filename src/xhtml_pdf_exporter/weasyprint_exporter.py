import logging
from pathlib import Path
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# A4 dimensions
A4_WIDTH_PX = 794  # Base width in pixels
A4_RATIO = 1.414  # Standard A4 ratio (297mm / 210mm)
A4_PORTRAIT = (A4_WIDTH_PX, int(A4_WIDTH_PX * A4_RATIO))  # 794 x 1123
A4_LANDSCAPE = (int(A4_WIDTH_PX * A4_RATIO), A4_WIDTH_PX)  # 1123 x 794

# A4 dimensions in inches for WeasyPrint
A4_PORTRAIT_IN = ("8.27in", "11.69in")  # width x height
A4_LANDSCAPE_IN = ("11.69in", "8.27in")  # width x height


def _get_pf_orientation(page) -> str | None:
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


def determine_orientation(input_file: Path, max_retries: int = 2) -> str:
    """
    Determine page orientation using Playwright's rendering engine.
    Includes retry mechanism and improved error handling.
    """
    chromium_args = [
        "--no-sandbox",
        "--disable-extensions",
        "--disable-software-rasterizer",
        "--disable-gpu-compositing",
        "--disable-setuid-sandbox",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--memory-pressure-off",
        "--disable-web-security",
    ]

    def try_get_orientation(p, attempt: int = 1) -> str:
        logger.info(f"Attempting orientation detection (attempt {attempt}/{max_retries + 1})")
        browser = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=chromium_args,
                chromium_sandbox=False,
                timeout=20000,  # 20 second timeout for browser launch
            )

            context = browser.new_context(
                viewport={"width": A4_LANDSCAPE[0], "height": A4_LANDSCAPE[1]},
                bypass_csp=True,
            )

            page = context.new_page()

            # Set shorter timeouts for page operations
            page.set_default_timeout(10000)  # 10 seconds

            try:
                # Load the page with basic load state
                logger.info("Loading page...")
                page.goto(
                    f"file://{input_file.absolute()}",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )

                # Wait for basic load states
                page.wait_for_load_state("domcontentloaded")

                # Check .pf elements first
                logger.info("Checking .pf elements...")
                pf_orientation = _get_pf_orientation(page)
                if pf_orientation:
                    logger.info(f"Orientation from .pf elements: {pf_orientation}")
                    return pf_orientation

                # Fallback to content dimensions with more robust checks
                logger.info("Measuring content dimensions...")
                dims = page.evaluate("""() => {
                    // Helper to get element dimensions
                    const getDimensions = (el) => {
                        const rect = el.getBoundingClientRect();
                        const styles = window.getComputedStyle(el);
                        return {
                            width: rect.width || parseFloat(styles.width),
                            height: rect.height || parseFloat(styles.height)
                        };
                    };

                    // Try .pf elements first
                    const pfElements = document.querySelectorAll('.pf');
                    if (pfElements.length) {
                        const pfDims = Array.from(pfElements).map(getDimensions);
                        return {
                            width: Math.max(...pfDims.map(d => d.width)),
                            height: Math.max(...pfDims.map(d => d.height))
                        };
                    }

                    // Try elements with 'page' in class name
                    const pageElements = document.querySelectorAll('[class*="page"]');
                    if (pageElements.length) {
                        const pageDims = Array.from(pageElements).map(getDimensions);
                        return {
                            width: Math.max(...pageDims.map(d => d.width)),
                            height: Math.max(...pageDims.map(d => d.height))
                        };
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
                }""")

                width, height = dims["width"], dims["height"]
                logger.info(f"Content dimensions: {width}x{height}")

                if width <= 0 or height <= 0:
                    logger.warning("Invalid dimensions detected, defaulting to portrait")
                    return "portrait"

                if width >= 400:  # Basic sanity check
                    if width > A4_WIDTH_PX * 1.2:  # 20% larger than A4 width → likely landscape
                        logger.info(
                            f"Content width ({width}px) exceeds A4 width threshold, indicating landscape orientation"
                        )
                        return "landscape"
                    logger.info(f"Content width ({width}px) suggests portrait orientation")
                    return "portrait"

                logger.info("Width too small, defaulting to portrait orientation")
                return "portrait"

            except Exception as e:
                logger.error(f"Page operation failed: {str(e)}")
                raise

        except Exception as e:
            if attempt <= max_retries:
                logger.warning(f"Attempt {attempt} failed: {str(e)}. Retrying...")
                import gc
                gc.collect()  # Force garbage collection before retry
                return try_get_orientation(p, attempt + 1)
            logger.error(f"All attempts failed. Last error: {str(e)}")
            return "portrait"  # Default to portrait after all retries fail

        finally:
            if browser:
                try:
                    browser.close()
                    logger.info("Browser closed successfully")
                except Exception as e:
                    logger.warning(f"Error closing browser: {str(e)}")

    with sync_playwright() as p:
        return try_get_orientation(p)


def export_pdf(input_file: str, output_file: str):
    """
    Export XHTML to PDF using WeasyPrint, with orientation determined
    by Playwright's rendering engine.
    """
    from weasyprint import CSS, HTML

    file_path = Path(input_file)
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    orientation = determine_orientation(file_path)
    if orientation == "landscape":
        page_size = A4_LANDSCAPE_IN
    else:
        page_size = A4_PORTRAIT_IN

    logger.info(f"Using page size {page_size} with WeasyPrint.")

    # WeasyPrint CSS configuration with comprehensive page break handling
    custom_css = f"""
    /* Base page configuration */
    @page {{
        size: {page_size[0]} {page_size[1]};
        margin: 0.5in 0.5in 0.5in 0.5in;
    }}

    /* Handle explicit page containers */
    .pf {{
        page-break-after: always;
        page-break-inside: avoid;
        margin: 0;
        padding: 0;
    }}

    /* Handle elements with 'page' in class name */
    [class*="page"] {{
        page-break-after: always;
        page-break-inside: avoid;
    }}

    /* Support modern break properties */
    [style*="break-before"], [style*="break-after"], [style*="break-inside"],
    [style*="page-break-before"], [style*="page-break-after"], [style*="page-break-inside"] {{
        /* Preserve original break behavior */
    }}

    /* Prevent unwanted breaks inside key elements */
    table, figure, img {{
        page-break-inside: avoid;
    }}

    /* Enable smart breaking for elements without explicit breaks */
    body {{
        orphans: 2;
        widows: 2;
    }}

    /* Preserve original document print styles */
    @media print {{
        * {{
            /* Ensure print-specific styles are respected */
            print-color-adjust: exact;
            -webkit-print-color-adjust: exact;
        }}
    }}
    """

    # Read the XHTML and generate PDF
    HTML(filename=str(file_path)).write_pdf(
        target=output_file, stylesheets=[CSS(string=custom_css)]
    )
    logger.info(f"PDF exported to {output_file} successfully.")


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Convert XHTML to PDF using WeasyPrint."
    )
    parser.add_argument("input_file", help="Input XHTML file path")
    parser.add_argument("output_file", help="Output PDF file path")
    args = parser.parse_args()

    export_pdf(args.input_file, args.output_file)


if __name__ == "__main__":
    main()
