import logging
import re

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Set up file handler for detailed logging
file_handler = logging.FileHandler('print_detection.log', mode='w')  # 'w' mode to start fresh each run
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Set up console handler for minimal output
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

# Set overall logger level to DEBUG to capture everything
logger.setLevel(logging.DEBUG)

def extract_dimensions_from_style(style_content):
    """
    Extract dimensions from CSS style content.
    Args:
        style_content (str): CSS style content
    Returns:
        tuple: (width, height) in pixels or None if not found
    """
    # Look for width/height in various formats
    width_match = re.search(r'width:\s*(\d+)(?:px|pt|mm)?', style_content)
    height_match = re.search(r'height:\s*(\d+)(?:px|pt|mm)?', style_content)

    if width_match and height_match:
        try:
            width = int(width_match.group(1))
            height = int(height_match.group(1))
            logger.debug(f"Found dimensions in style: {width}x{height}")
            return width, height
        except ValueError:
            logger.warning("Failed to parse dimensions from style")
    return None, None

def check_print_layout(page, xhtml_path):
    """Check CSS print layout properties"""
    logger.debug(f"[{xhtml_path}] Checking CSS Print Layout:")
    layout_info = page.evaluate("""() => {
        const styles = window.getComputedStyle(document.body);
        return {
            size: styles.getPropertyValue('size'),
            orientation: styles.getPropertyValue('orientation'),
            pageBreakBefore: styles.getPropertyValue('page-break-before'),
            pageBreakAfter: styles.getPropertyValue('page-break-after')
        };
    }""")
    for key, value in layout_info.items():
        logger.debug(f"  {key}: {value}")
    return layout_info

def check_page_rules(page, xhtml_path):
    """Check @page rules in stylesheets"""
    logger.debug(f"[{xhtml_path}] Checking @page Rules:")
    page_rules = page.evaluate("""() => {
        const pageRules = [];
        for (const sheet of document.styleSheets) {
            try {
                for (const rule of sheet.cssRules) {
                    if (rule.type === CSSRule.PAGE_RULE) {
                        pageRules.push({
                            selector: rule.selectorText,
                            size: rule.style.size,
                            margin: rule.style.margin
                        });
                    }
                }
            } catch (e) {}
        }
        return pageRules;
    }""")
    for rule in page_rules:
        logger.debug(f"  Rule: {rule}")
    return page_rules

def check_page_elements(page, xhtml_path):
    """Check page-related elements and their properties"""
    logger.debug(f"[{xhtml_path}] Checking Page Elements:")
    elements = page.evaluate("""() => {
        const getElementInfo = (el) => ({
            className: el.className,
            id: el.id,
            width: el.offsetWidth,
            height: el.offsetHeight,
            style: {
                width: el.style.width,
                height: el.style.height,
                pageBreakBefore: el.style.pageBreakBefore,
                pageBreakAfter: el.style.pageBreakAfter
            }
        });

        const results = {
            pages: Array.from(document.querySelectorAll('.page, .pdf-page, .sheet')).map(getElementInfo),
            pageBreaks: Array.from(document.querySelectorAll('[style*="page-break"], [class*="page-break"]')).map(getElementInfo),
            pageContainers: Array.from(document.querySelectorAll('.pf, [class*="page-container"]')).map(getElementInfo)
        };
        return results;
    }""")
    for key, elements_list in elements.items():
        logger.debug(f"  {key}: {len(elements_list)} elements found")
        for el in elements_list:
            logger.debug(f"    Element: {el}")
    return elements

def check_print_media(page, xhtml_path):
    """Check print media queries and related styles"""
    logger.debug(f"[{xhtml_path}] Checking Print Media Features:")
    media_info = page.evaluate("""() => {
        const mediaQuery = window.matchMedia('print');
        const printStyles = [];
        const printRules = [];

        for (const sheet of document.styleSheets) {
            try {
                for (const rule of sheet.cssRules) {
                    if (rule.type === CSSRule.MEDIA_RULE) {
                        if (rule.conditionText.includes('print')) {
                            printStyles.push(rule.cssText);
                            // Extract specific print-related properties
                            for (const styleRule of rule.cssRules) {
                                if (styleRule.type === CSSRule.STYLE_RULE) {
                                    const style = styleRule.style;
                                    if (style.size || style.orientation ||
                                        style.pageBreakBefore || style.pageBreakAfter) {
                                        printRules.push({
                                            selector: styleRule.selectorText,
                                            size: style.size,
                                            orientation: style.orientation,
                                            pageBreakBefore: style.pageBreakBefore,
                                            pageBreakAfter: style.pageBreakAfter
                                        });
                                    }
                                }
                            }
                        }
                    }
                }
            } catch (e) {}
        }
        return {
            printMediaSupported: mediaQuery.matches,
            printStyles: printStyles,
            printRules: printRules,
            hasPrintStylesheet: Array.from(document.styleSheets).some(sheet =>
                sheet.media?.mediaText?.includes('print')
            )
        };
    }""")
    logger.debug(f"  Print Media Supported: {media_info['printMediaSupported']}")
    logger.debug(f"  Has Print Stylesheet: {media_info.get('hasPrintStylesheet', False)}")
    for style in media_info['printStyles']:
        logger.debug(f"  Print Style: {style}")
    for rule in media_info.get('printRules', []):
        logger.debug(f"  Print Rule: {rule}")
    return media_info

def check_chromium_print_settings(page, xhtml_path):
    """Check all possible Chromium print settings"""
    logger.debug(f"[{xhtml_path}] Checking Chromium Print Settings:")
    settings = page.evaluate("""() => {
        const settings = {
            // Check @page size and orientation
            pageRules: Array.from(document.styleSheets).flatMap(sheet => {
                try {
                    return Array.from(sheet.cssRules).filter(rule =>
                        rule.type === CSSRule.PAGE_RULE
                    ).map(rule => ({
                        size: rule.style.size,
                        orientation: rule.style.orientation,
                        margin: rule.style.margin
                    }));
                } catch (e) {
                    return [];
                }
            }),

            // Check print-specific elements
            hasPrintStylesheet: Array.from(document.styleSheets).some(sheet =>
                sheet.media?.mediaText?.includes('print')
            ),

            // Check page dimensions in points (1pt = 1/72 inch)
            pageDimensions: {
                width: Math.round(document.documentElement.offsetWidth * 72 / 96),  // px to pt
                height: Math.round(document.documentElement.offsetHeight * 72 / 96)
            },

            // Check common paper sizes (with tolerance)
            paperSizes: {
                A4: {
                    portrait: Math.abs(document.documentElement.offsetWidth / document.documentElement.offsetHeight - 0.707) < 0.1,
                    landscape: Math.abs(document.documentElement.offsetHeight / document.documentElement.offsetWidth - 0.707) < 0.1
                },
                Letter: {
                    portrait: Math.abs(document.documentElement.offsetWidth / document.documentElement.offsetHeight - 0.773) < 0.1,
                    landscape: Math.abs(document.documentElement.offsetHeight / document.documentElement.offsetWidth - 0.773) < 0.1
                },
                Legal: {
                    portrait: Math.abs(document.documentElement.offsetWidth / document.documentElement.offsetHeight - 0.613) < 0.1,
                    landscape: Math.abs(document.documentElement.offsetHeight / document.documentElement.offsetWidth - 0.613) < 0.1
                }
            },

            // Enhanced orientation detection
            orientationHints: {
                html: {
                    style: document.documentElement.style.orientation,
                    computed: getComputedStyle(document.documentElement).orientation
                },
                body: {
                    style: document.body.style.orientation,
                    computed: getComputedStyle(document.body).orientation
                },
                meta: document.querySelector('meta[name="viewport"]')?.content.includes('orientation='),
                cssPage: document.querySelector('style')?.textContent.match(/@page[^{]*{[^}]*orientation\s*:\s*([^;}]+)/)?.[1]
            },

            // Check for specific print-related elements
            printElements: {
                pageContainers: document.querySelectorAll('.pf, [class*="page-container"]').length,
                pageBreaks: document.querySelectorAll('[style*="page-break"], [class*="page-break"]').length,
                printSections: document.querySelectorAll('[class*="print"], [id*="print"]').length
            }
        };

        // Add viewport meta info with detailed parsing
        const viewportMeta = document.querySelector('meta[name="viewport"]');
        if (viewportMeta) {
            const content = viewportMeta.content;
            settings.viewport = {
                raw: content,
                parsed: content.split(',').reduce((acc, pair) => {
                    const [key, value] = pair.trim().split('=');
                    acc[key] = value;
                    return acc;
                }, {})
            };
        }

        return settings;
    }""")

    logger.debug(f"  Print Settings:")
    logger.debug(f"    Page Rules: {settings.get('pageRules', [])}")
    logger.debug(f"    Has Print Stylesheet: {settings.get('hasPrintStylesheet', False)}")
    logger.debug(f"    Page Dimensions: {settings.get('pageDimensions', {})}")
    logger.debug(f"    Paper Sizes Match: {settings.get('paperSizes', {})}")
    logger.debug(f"    Orientation Hints: {settings.get('orientationHints', {})}")
    logger.debug(f"    Print Elements: {settings.get('printElements', {})}")
    logger.debug(f"    Viewport Settings: {settings.get('viewport', {})}")

    return settings

def get_background_image_dimensions(xhtml_path):
    """
    Extracts the background image dimensions from an XHTML file using Playwright.
    Handles orientation based on the specific file being processed.

    Args:
        xhtml_path (str): The path to the XHTML file.

    Returns:
        tuple: A tuple containing the width and height of the background image,
               or None if not found or if there was an error.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(f"file://{xhtml_path}")

            # Check all possible print-related settings
            print_layout = check_print_layout(page, xhtml_path)
            page_rules = check_page_rules(page, xhtml_path)
            page_elements = check_page_elements(page, xhtml_path)
            print_media = check_print_media(page, xhtml_path)
            chromium_settings = check_chromium_print_settings(page, xhtml_path)

            # Use BeautifulSoup to parse the HTML content from the page
            html_content = page.content()
            logger.debug("Parsing HTML content")
            soup = BeautifulSoup(html_content, "html.parser")

            # Find the #page-container element
            page_container = soup.find(id="page-container")
            if page_container:
                logger.debug("Found #page-container element")

                # Get detailed dimensions from multiple sources
                dimensions = page.evaluate("""() => {
                    const container = document.getElementById('page-container');
                    const dims = {
                        container: null,
                        document: {
                            scroll: { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
                            client: { width: document.documentElement.clientWidth, height: document.documentElement.clientHeight },
                            offset: { width: document.documentElement.offsetWidth, height: document.documentElement.offsetHeight }
                        },
                        body: {
                            scroll: { width: document.body.scrollWidth, height: document.body.scrollHeight },
                            client: { width: document.body.clientWidth, height: document.body.clientHeight },
                            offset: { width: document.body.offsetWidth, height: document.body.offsetHeight }
                        }
                    };

                    if (container) {
                        const style = window.getComputedStyle(container);
                        dims.container = {
                            computed: { width: parseInt(style.width), height: parseInt(style.height) },
                            scroll: { width: container.scrollWidth, height: container.scrollHeight },
                            client: { width: container.clientWidth, height: container.clientHeight },
                            offset: { width: container.offsetWidth, height: container.offsetHeight }
                        };
                    }

                    return dims;
                }""")

                # Log all dimensions
                if dimensions:
                    logger.debug(f"[{xhtml_path}] Document dimensions:")
                    logger.debug(f"  Scroll: {dimensions['document']['scroll']['width']}x{dimensions['document']['scroll']['height']}")
                    logger.debug(f"  Client: {dimensions['document']['client']['width']}x{dimensions['document']['client']['height']}")
                    logger.debug(f"  Offset: {dimensions['document']['offset']['width']}x{dimensions['document']['offset']['height']}")

                    logger.debug(f"[{xhtml_path}] Body dimensions:")
                    logger.debug(f"  Scroll: {dimensions['body']['scroll']['width']}x{dimensions['body']['scroll']['height']}")
                    logger.debug(f"  Client: {dimensions['body']['client']['width']}x{dimensions['body']['client']['height']}")
                    logger.debug(f"  Offset: {dimensions['body']['offset']['width']}x{dimensions['body']['offset']['height']}")

                    if dimensions["container"]:
                        logger.debug(f"[{xhtml_path}] Container dimensions:")
                        logger.debug(f"  Computed: {dimensions['container']['computed']['width']}x{dimensions['container']['computed']['height']}")
                        logger.debug(f"  Scroll: {dimensions['container']['scroll']['width']}x{dimensions['container']['scroll']['height']}")
                        logger.debug(f"  Client: {dimensions['container']['client']['width']}x{dimensions['container']['client']['height']}")
                        logger.debug(f"  Offset: {dimensions['container']['offset']['width']}x{dimensions['container']['offset']['height']}")

                # Use container dimensions if available
                container_dims = dimensions["container"]["computed"] if dimensions["container"] else None
                if container_dims and container_dims["width"] > 0 and container_dims["height"] > 0:
                    width, height = container_dims["width"], container_dims["height"]
                    logger.debug(f"Container computed dimensions (raw): {width}x{height}")
                    logger.debug(f"[{xhtml_path}] Container computed dimensions (raw): {width}x{height}")
                    logger.debug(f"[{xhtml_path}] Using dimensions from container element")
                    return width, height

                # Try to find background image and dimensions in css styles
                for style_tag in soup.find_all("style"):
                    if not style_tag.string:
                        continue

                    style_content = style_tag.string
                    if "#page-container" in style_content:
                        # Try to get dimensions from style first
                        width, height = extract_dimensions_from_style(style_content)
                        if width and height:
                            logger.debug(f"[{xhtml_path}] Style dimensions (raw): {width}x{height}")
                            logger.debug(f"[{xhtml_path}] Using dimensions from style")
                            return width, height

                        # If no dimensions in style, look for background image
                        if "background-image" in style_content:
                            start_index = style_content.find("url(data:image/svg+xml;base64,")
                            if start_index != -1:
                                start_index += len("url(data:image/svg+xml;base64,")
                                end_index = style_content.find(")", start_index)

                                if end_index != -1:
                                    encoded_svg = style_content[start_index:end_index]

                                    # Extract width and height from the svg
                                    try:
                                        # First try to decode base64 content
                                        svg_content = page.evaluate(f'atob("{encoded_svg}")')
                                        logger.debug(f"[{xhtml_path}] SVG Content: %s", svg_content)

                                        # Try parsing with lxml first
                                        try:
                                            svg_soup = BeautifulSoup(svg_content, "lxml-xml")
                                        except Exception as xml_error:
                                            logger.warning("lxml-xml parsing failed: %s. Falling back to html.parser", xml_error)
                                            svg_soup = BeautifulSoup(svg_content, "html.parser")

                                        # Look for viewBox first as it might contain the actual dimensions
                                        svg_element = svg_soup.find("svg")
                                        if svg_element and not isinstance(svg_element, str):
                                            # Try to get viewBox dimensions
                                            viewbox = svg_element.get("viewBox", "") if hasattr(svg_element, "get") else ""
                                            if viewbox and isinstance(viewbox, str):
                                                try:
                                                    viewbox_parts = viewbox.strip().split()
                                                    if len(viewbox_parts) >= 4:
                                                        vb_width = float(viewbox_parts[2])
                                                        vb_height = float(viewbox_parts[3])
                                                        logger.debug(f"[{xhtml_path}] Found viewBox dimensions: {vb_width}x{vb_height}")
                                                        # Convert to integers
                                                        width = int(vb_width)
                                                        height = int(vb_height)
                                                        logger.debug(f"[{xhtml_path}] ViewBox dimensions (raw): {width}x{height}")
                                                        logger.debug(f"[{xhtml_path}] Using dimensions from SVG viewBox")
                                                        # Check for reasonable page dimensions (A4 is typically 595x842 points)
                                                        if width >= 400 and height >= 600:
                                                            return width, height
                                                except (ValueError, IndexError) as e:
                                                    logger.warning(f"Failed to parse viewBox: {e}")

                                            # Fallback to width/height attributes
                                            if hasattr(svg_element, "get"):
                                                width = svg_element.get("width", "")
                                                height = svg_element.get("height", "")
                                            else:
                                                width = height = ""

                                            if width and height and isinstance(width, str) and isinstance(height, str):
                                                # Try to convert to integers, strip any units
                                                try:
                                                    width = int(''.join(filter(str.isdigit, width)))
                                                    height = int(''.join(filter(str.isdigit, height)))
                                                    logger.debug(f"[{xhtml_path}] Found SVG attribute dimensions: {width}x{height}")
                                                    logger.debug(f"[{xhtml_path}] SVG dimensions (raw): {width}x{height}")
                                                    logger.debug(f"[{xhtml_path}] Using dimensions from SVG attributes")
                                                    # Check for reasonable page dimensions
                                                    if width >= 400 and height >= 600:
                                                        return width, height
                                                except ValueError as e:
                                                    logger.error("Failed to parse SVG dimensions: %s", e)
                                        else:
                                            logger.error("No SVG element found in content")
                                    except Exception as e:
                                        logger.error("Failed to process SVG content: %s", e)

            # Enhanced content dimension detection with multiple fallbacks
            content_dims = page.evaluate("""() => {
                // Try to find dimensions from various content elements
                const selectors = [
                    '.page-content', '#page-content', '.content', '#content',
                    '[class*="page"]', '[id*="page"]',
                    '[class*="pdf"]', '[id*="pdf"]',
                    'article', 'main', '.main', '#main'
                ];

                // Try each selector
                for (const selector of selectors) {
                    const element = document.querySelector(selector);
                    if (element) {
                        const rect = element.getBoundingClientRect();
                        const computed = window.getComputedStyle(element);
                        const dims = {
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                            source: selector,
                            computed: {
                                width: parseInt(computed.width),
                                height: parseInt(computed.height)
                            },
                            scroll: {
                                width: element.scrollWidth,
                                height: element.scrollHeight
                            }
                        };
                        // Only return if dimensions are reasonable
                        if (dims.width >= 400 && dims.height >= 600) {
                            return dims;
                        }
                    }
                }

                // If no content elements found or dimensions unreasonable, try document/body
                const docElement = document.documentElement;
                const body = document.body;

                // Get the maximum dimensions from various sources
                const dims = {
                    width: Math.max(
                        docElement.scrollWidth,
                        docElement.offsetWidth,
                        docElement.clientWidth,
                        body.scrollWidth,
                        body.offsetWidth,
                        body.clientWidth,
                        window.innerWidth
                    ),
                    height: Math.max(
                        docElement.scrollHeight,
                        docElement.offsetHeight,
                        docElement.clientHeight,
                        body.scrollHeight,
                        body.offsetHeight,
                        body.clientHeight,
                        window.innerHeight
                    ),
                    source: 'document/body'
                };

                return dims;
            }""")

            if content_dims:
                width = content_dims["width"]
                height = content_dims["height"]
                logger.debug(f"[{xhtml_path}] Content dimensions from {content_dims['source']}:")
                logger.debug(f"  Raw dimensions: {width}x{height}")

                if 'computed' in content_dims:
                    logger.debug(f"  Computed dimensions: {content_dims['computed']['width']}x{content_dims['computed']['height']}")
                if 'scroll' in content_dims:
                    logger.debug(f"  Scroll dimensions: {content_dims['scroll']['width']}x{content_dims['scroll']['height']}")

                # Check if dimensions are reasonable for a page
                if width >= 400 and height >= 600:
                    # Check orientation hints from earlier checks
                    orientation_hints = chromium_settings.get('orientationHints', {})
                    is_landscape = any([
                        orientation_hints.get('html', {}).get('style') == 'landscape',
                        orientation_hints.get('html', {}).get('computed') == 'landscape',
                        orientation_hints.get('body', {}).get('style') == 'landscape',
                        orientation_hints.get('body', {}).get('computed') == 'landscape',
                        orientation_hints.get('cssPage') == 'landscape'
                    ])

                    # If landscape orientation is detected and current dimensions are portrait,
                    # swap width and height
                    if is_landscape and width < height:
                        logger.debug(f"[{xhtml_path}] Landscape orientation detected, swapping dimensions")
                        width, height = height, width

                    logger.debug(f"[{xhtml_path}] Final dimensions: {width}x{height}")
                    return width, height

            browser.close()
            return None, None

    except Exception as e:
        logger.error("An error occurred while processing the XHTML file: %s", e)
        return None, None

if __name__ == "__main__":
    xhtmls_files = [
        # "/Users/jakit/simonas/projects/xhtml-pdf/assets/xhtml/sample_0.xhtml",
        # "/Users/jakit/simonas/projects/xhtml-pdf/assets/xhtml/sample_1.xhtml",
        # "/Users/jakit/simonas/projects/xhtml-pdf/assets/xhtml/sample_2.xhtml",
        "/Users/jakit/simonas/projects/xhtml-pdf/assets/xhtml/sample_3.xhtml",
        # "/Users/jakit/simonas/projects/xhtml-pdf/assets/xhtml/sample_4.xhtml",
    ]

    for xhtml_file in xhtmls_files:
        width, height = get_background_image_dimensions(xhtml_file)

        if width is not None and height is not None:
            print(f"{xhtml_file}: Background image dimensions: Width = {width}, Height = {height}")
        else:
            print(f"{xhtml_file}: Could not extract background image dimensions.")
