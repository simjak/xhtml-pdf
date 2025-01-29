import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Pattern, TypedDict

from bs4 import BeautifulSoup, Tag

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

            results: PatternResults = {
                "class_patterns": self._analyze_class_patterns(soup),
                "style_patterns": self._analyze_style_patterns(soup),
                "id_patterns": self._analyze_id_patterns(soup),
            }

            # Print formatted results
            logger.info(f"\nPage Break Analysis for {xhtml_path}:")
            logger.info("-" * 40)

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

    def _detect_page_pattern(self, soup: BeautifulSoupType) -> Optional[Dict[str, str]]:
        """Detect which page marker pattern is used in the document."""
        # First try class-based patterns
        for pattern in self.page_patterns:
            if soup.find("div", pattern):
                logger.info(f"Using class-based pattern: {pattern}")
                return pattern

        # Then try style-based patterns
        for name, predicate in self.style_patterns.items():
            if soup.find(self._make_style_predicate(predicate)):
                logger.info(f"Using style-based pattern: {name}")
                return {"style": name}

        # Finally try ID-based patterns
        for pattern in self.id_patterns:
            if soup.find(self._make_id_predicate(pattern)):
                logger.info(f"Using ID-based pattern: {pattern.pattern}")
                return {"id": pattern.pattern}

        return None

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

    def _extract_pages(
        self, soup: BeautifulSoupType, pattern: Dict[str, str]
    ) -> List[str]:
        """Extract individual pages based on the detected pattern."""
        pages = []
        if "style" in pattern:
            predicate = self.style_patterns[pattern["style"]]
            page_elements = soup.find_all(self._make_style_predicate(predicate))
        elif "id" in pattern:
            id_pattern = re.compile(pattern["id"])
            page_elements = soup.find_all(self._make_id_predicate(id_pattern))
        else:
            page_elements = soup.find_all("div", pattern)

        for page_element in page_elements:
            pages.append(str(page_element))

        return pages

    def _create_output_path(self, input_path: str, page_num: int) -> str:
        """Create output path for extracted page."""
        path_obj = Path(input_path)
        base_output_dir = path_obj.parent / "extracted_pages"
        base_output_dir.mkdir(exist_ok=True)
        report_dir = base_output_dir / path_obj.stem
        report_dir.mkdir(exist_ok=True)
        filename = f"page_{page_num}{path_obj.suffix}"
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

            # Detect page pattern
            pattern = self._detect_page_pattern(soup)
            if not pattern:
                logger.error("No supported page pattern found in document")
                return []

            # Extract common elements
            head_content = self._extract_head_content(soup)
            namespaces = self._get_namespaces(soup)

            # Extract pages
            pages = self._extract_pages(soup, pattern)
            if not pages:
                logger.error("No pages found in document")
                return []

            # Save individual pages
            output_paths = []
            for i, page_content in enumerate(pages, 1):
                output_path = self._create_output_path(xhtml_path, i)
                page_xhtml = self._create_page_xhtml(
                    head_content=head_content,
                    page_content=page_content,
                    namespaces=namespaces,
                )
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(page_xhtml)
                output_paths.append(output_path)

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
                for path in output_files:
                    logger.info(f"- {path}")
            else:
                logger.error("Failed to extract pages")
