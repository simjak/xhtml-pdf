import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Pattern, Set, Tuple, TypedDict

from bs4 import BeautifulSoup, Tag

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Type definitions for better type checking
class PatternDetails(TypedDict):
    count: int
    example: str


class PatternResults(TypedDict):
    class_patterns: Dict[str, PatternDetails]
    style_patterns: Dict[str, PatternDetails]
    id_patterns: Dict[str, PatternDetails]


StylePredicate = Callable[[Any], bool]
TagPredicate = Callable[[Tag], bool]
BeautifulSoupType = BeautifulSoup  # Type alias for type hints


class PageExtractor:
    """Extract pages from XHTML reports into separate files."""

    def __init__(self):
        # Class-based patterns
        self.page_patterns = [
            {"class": "pf"},  # Common pattern
            {"class": "page"},  # Basic page marker
            {"class": "pageView"},  # Viewer-specific
            {"class": "pp-class-0"},  # Special format
            {"class": "sheet"},  # Sheet-based
            {"class": "pdf-page"},  # PDF conversion
            {"class": "page-container"},  # Container pattern
            {"class": "page-wrapper"},  # Wrapper pattern
        ]

        # Style-based patterns
        self.style_patterns = {
            "page-break-before": lambda x: bool(x and "page-break-before" in str(x)),
            "page-break-after": lambda x: bool(x and "page-break-after" in str(x)),
            "break-before": lambda x: bool(x and "break-before" in str(x)),
            "break-after": lambda x: bool(x and "break-after" in str(x)),
        }

        # ID-based patterns
        self.id_patterns = [
            re.compile(r"pf\d+"),  # e.g., pf1, pf2
            re.compile(r"page\d+"),  # e.g., page1, page2
            re.compile(r"pg\d+"),  # e.g., pg1, pg2
        ]

        # Page number extraction patterns - ensure \d+ matches any number of digits
        self.page_number_patterns = [
            re.compile(r"pf(\d+)"),  # e.g., pf1, pf123
            re.compile(r"page[_-]?(\d+)"),  # e.g., page1, page_123
            re.compile(r"pg[_-]?(\d+)"),  # e.g., pg1, pg_123
            re.compile(r"^(\d+)$"),  # e.g., 1, 123
        ]

    def _extract_page_number(self, element: Tag) -> Optional[int]:
        """
        Extract page number from element ID, class, or data attributes.

        Args:
            element: BeautifulSoup Tag element

        Returns:
            Page number if found, None otherwise
        """
        # Try ID-based extraction
        element_id = element.get("id")
        if isinstance(element_id, str) and element_id:
            logger.debug(f"Checking ID: {element_id}")
            for pattern in self.page_number_patterns:
                if match := pattern.search(element_id):
                    try:
                        number = int(match.group(1))
                        logger.debug(f"Found page number {number} in ID")
                        return number
                    except ValueError:
                        continue

        # Try data attributes
        for attr in ["data-page-number", "data-page", "page-number"]:
            value = element.get(attr)
            if isinstance(value, str) and value:
                logger.debug(f"Checking attribute {attr}: {value}")
                try:
                    number = int(value)
                    logger.debug(f"Found page number {number} in attribute")
                    return number
                except ValueError:
                    continue

        # Try class-based extraction
        element_classes = element.get("class", [])
        if isinstance(element_classes, list):
            for class_name in element_classes:
                if isinstance(class_name, str):
                    logger.debug(f"Checking class: {class_name}")
                    for pattern in self.page_number_patterns:
                        if match := pattern.search(class_name):
                            try:
                                number = int(match.group(1))
                                logger.debug(f"Found page number {number} in class")
                                return number
                            except ValueError:
                                continue

        return None

    def _find_all_page_elements(self, soup: BeautifulSoupType) -> List[Tag]:
        """
        Find all page elements using multiple patterns.

        Args:
            soup: BeautifulSoup object

        Returns:
            List of found page elements
        """
        pages: Set[Tag] = set()  # Use set to avoid duplicates

        # Try class-based patterns
        for pattern in self.page_patterns:
            elements = soup.find_all("div", pattern)
            if elements:
                logger.debug(f"Found {len(elements)} pages with pattern {pattern}")
            pages.update(elements)

        # Try style-based patterns
        for name, predicate in self.style_patterns.items():
            elements = soup.find_all(self._make_style_predicate(predicate))
            if elements:
                logger.debug(f"Found {len(elements)} pages with style {name}")
            pages.update(elements)

        # Try ID-based patterns
        for pattern in self.id_patterns:
            elements = soup.find_all(self._make_id_predicate(pattern))
            if elements:
                logger.debug(f"Found {len(elements)} pages with ID pattern {pattern.pattern}")
            pages.update(elements)

        logger.info(f"Found {len(pages)} total page elements")
        return list(pages)

    def _verify_page_sequence(self, pages: List[Tag]) -> List[Tag]:
        """
        Verify and sort pages by number, identify missing pages.

        Args:
            pages: List of page elements

        Returns:
            Sorted list of page elements
        """
        numbered_pages: List[Tuple[int, Tag]] = []
        unnumbered_pages: List[Tag] = []
        page_numbers: List[int] = []

        # Extract all page numbers first
        logger.info("Extracting page numbers...")
        for page in pages:
            element_id = page.get('id', '')
            if number := self._extract_page_number(page):
                numbered_pages.append((number, page))
                page_numbers.append(number)
                logger.debug(f"Found page number {number} from element with id: {element_id}")
            else:
                unnumbered_pages.append(page)
                logger.debug(f"No page number found in element with id: {element_id}")

        # Log page number range
        if page_numbers:
            min_num = min(page_numbers)
            max_num = max(page_numbers)
            logger.info(f"Found page numbers ranging from {min_num} to {max_num}")
            logger.info(f"Total numbered pages: {len(page_numbers)}")
            logger.debug(f"Page number sequence: {sorted(page_numbers)}")

        # Sort numbered pages
        numbered_pages.sort(key=lambda x: x[0])

        # Check for gaps in numbered pages
        if numbered_pages:
            min_page = numbered_pages[0][0]
            max_page = numbered_pages[-1][0]
            expected_range = set(range(min_page, max_page + 1))
            found_numbers = {n for n, _ in numbered_pages}
            missing = expected_range - found_numbers

            if missing:
                logger.warning(f"Missing page numbers detected: {sorted(missing)}")
                logger.warning("This might indicate pages are not being properly detected")
                logger.warning(f"Expected {len(expected_range)} pages, found {len(found_numbers)}")

        # Combine numbered and unnumbered pages
        result = [page for _, page in numbered_pages]
        if unnumbered_pages:
            logger.warning(f"Found {len(unnumbered_pages)} pages without numbers")
            result.extend(unnumbered_pages)

        logger.info(f"Final page count: {len(result)}")
        return result

    def _make_id_predicate(self, pattern: Pattern[str]) -> TagPredicate:
        """Create a predicate function for ID pattern matching."""

        def predicate(tag: Tag) -> bool:
            if not isinstance(tag, Tag):
                return False
            id_attr = tag.get("id", "")
            if not id_attr:
                return False
            return bool(pattern.match(str(id_attr)))

        return predicate

    def _make_style_predicate(self, style_check: StylePredicate) -> TagPredicate:
        """Create a predicate function for style pattern matching."""

        def predicate(tag: Tag) -> bool:
            if not isinstance(tag, Tag):
                return False
            style_attr = tag.get("style", "")
            return style_check(style_attr)

        return predicate

    def analyze_page_breaks(self, xhtml_path: str) -> PatternResults:
        """
        Analyze and report all page break patterns found in the XHTML.

        Args:
            xhtml_path: Path to the XHTML file

        Returns:
            Dictionary containing analysis results for each pattern type
        """
        try:
            with open(xhtml_path, "r", encoding="utf-8") as f:
                content = f.read()

            soup = BeautifulSoup(content, "lxml-xml")

            # Find all pages first
            all_pages = self._find_all_page_elements(soup)
            verified_pages = self._verify_page_sequence(all_pages)

            results: PatternResults = {
                "class_patterns": self._analyze_class_patterns(soup),
                "style_patterns": self._analyze_style_patterns(soup),
                "id_patterns": self._analyze_id_patterns(soup),
            }

            # Print formatted results
            logger.info(f"\nPage Break Analysis for {xhtml_path}:")
            logger.info("-" * 40)
            logger.info(f"Total pages found: {len(verified_pages)}")

            if results["class_patterns"]:
                logger.info("\nClass-based Patterns:")
                for pattern, details in results["class_patterns"].items():
                    logger.info(f'- class="{pattern}" : {details["count"]} instances')
                    if details["example"]:
                        logger.info(f"  Example: {details['example']}")

            if results["style_patterns"]:
                logger.info("\nStyle-based Patterns:")
                for pattern, details in results["style_patterns"].items():
                    logger.info(f"- {pattern} : {details['count']} instances")
                    if details["example"]:
                        logger.info(f"  Example: {details['example']}")

            if results["id_patterns"]:
                logger.info("\nID-based Patterns:")
                for pattern, details in results["id_patterns"].items():
                    logger.info(f"- {pattern} : {details['count']} matches")
                    if details["example"]:
                        logger.info(f"  Example: {details['example']}")

            return results

        except Exception as e:
            logger.error(f"Error analyzing page breaks: {str(e)}")
            return {"class_patterns": {}, "style_patterns": {}, "id_patterns": {}}

    def _analyze_class_patterns(
        self, soup: BeautifulSoupType
    ) -> Dict[str, PatternDetails]:
        """Analyze class-based page patterns."""
        results: Dict[str, PatternDetails] = {}
        for pattern in self.page_patterns:
            elements = soup.find_all("div", pattern)
            if elements:
                pattern_name = str(pattern.get("class", "unknown"))
                results[pattern_name] = {
                    "count": len(elements),
                    "example": str(elements[0])[:200] + "..." if elements else "",
                }
        return results

    def _analyze_style_patterns(
        self, soup: BeautifulSoupType
    ) -> Dict[str, PatternDetails]:
        """Analyze style-based page breaks."""
        results: Dict[str, PatternDetails] = {}
        for name, predicate in self.style_patterns.items():
            elements = soup.find_all(self._make_style_predicate(predicate))
            if elements:
                results[name] = {
                    "count": len(elements),
                    "example": str(elements[0])[:200] + "..." if elements else "",
                }
        return results

    def _analyze_id_patterns(
        self, soup: BeautifulSoupType
    ) -> Dict[str, PatternDetails]:
        """Analyze ID-based page patterns."""
        results: Dict[str, PatternDetails] = {}
        for pattern in self.id_patterns:
            elements = soup.find_all(self._make_id_predicate(pattern))
            if elements:
                results[pattern.pattern] = {
                    "count": len(elements),
                    "example": str(elements[0])[:200] + "..." if elements else "",
                }
        return results

    def _extract_head_content(self, soup: BeautifulSoupType) -> str:
        """Extract and format head content including styles."""
        head = soup.find("head")
        if not head:
            return ""
        return str(head)

    def _create_page_xhtml(
        self, head_content: str, page_content: str, namespaces: str = ""
    ) -> str:
        """Create a complete XHTML document for a single page."""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html {namespaces}>
{head_content}
<body>
{page_content}
</body>
</html>"""

    def _get_namespaces(self, soup: BeautifulSoupType) -> str:
        """Extract namespace declarations from the original document."""
        html_tag = soup.find("html")
        if isinstance(html_tag, Tag):
            namespaces = " ".join(
                [
                    f'{k}="{v}"'
                    for k, v in html_tag.attrs.items()
                    if k.startswith("xmlns") or k == "xml:lang"
                ]
            )
            return namespaces
        return ""

    def _create_output_path(self, input_path: str, page_num: int) -> str:
        """Create output path for extracted page."""
        path_obj = Path(input_path)
        base_output_dir = path_obj.parent / "extracted_pages"
        base_output_dir.mkdir(exist_ok=True)
        report_dir = base_output_dir / path_obj.stem
        report_dir.mkdir(exist_ok=True)
        filename = f"page_{page_num:03d}{path_obj.suffix}"  # Use 3-digit padding
        return str(report_dir / filename)

    def extract_pages(self, xhtml_path: str) -> List[str]:
        """
        Extract pages from an XHTML file into separate files.

        Args:
            xhtml_path: Path to the XHTML file

        Returns:
            List of paths to extracted page files
        """
        try:
            with open(xhtml_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Parse XHTML
            soup = BeautifulSoup(content, "lxml-xml")

            # First analyze page breaks
            self.analyze_page_breaks(xhtml_path)

            # Find and verify all pages
            all_pages = self._find_all_page_elements(soup)
            verified_pages = self._verify_page_sequence(all_pages)

            if not verified_pages:
                logger.error("No pages found in document")
                return []

            # Extract common elements
            head_content = self._extract_head_content(soup)
            namespaces = self._get_namespaces(soup)

            # Save individual pages
            output_paths = []
            for i, page_element in enumerate(verified_pages, 1):
                # Try to use actual page number if available
                page_num = self._extract_page_number(page_element) or i
                output_path = self._create_output_path(xhtml_path, page_num)
                page_xhtml = self._create_page_xhtml(
                    head_content=head_content,
                    page_content=str(page_element),
                    namespaces=namespaces,
                )
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(page_xhtml)
                output_paths.append(output_path)
                logger.info(f"Saved page {page_num} to {output_path}")

            logger.info(f"Successfully extracted {len(output_paths)} pages")
            return output_paths

        except Exception as e:
            logger.error(f"Error extracting pages: {str(e)}")
            return []


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)

    # Example usage
    extractor = PageExtractor()

    # Test with sample files
    sample_files = [
        "assets/xhtml/sample_0.xhtml",
        "assets/xhtml/sample_1.xhtml",
        "assets/xhtml/sample_2.xhtml",
        "assets/xhtml/sample_3.xhtml",
        "assets/xhtml/sample_4.xhtml",
    ]

    for sample_file in sample_files:
        if os.path.exists(sample_file):
            logger.info(f"\nProcessing {sample_file}")
            output_files = extractor.extract_pages(sample_file)

            if output_files:
                logger.info(f"Successfully extracted {len(output_files)} pages")
            else:
                logger.error("Failed to extract pages")
