import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union
from xml.etree import ElementTree as ET


class Orientation(Enum):
    PORTRAIT = auto()
    LANDSCAPE = auto()


class StyleType(Enum):
    CSS = auto()
    INLINE = auto()
    MIXED = auto()


class PageType(Enum):
    PF = auto()  # Page formatting element
    PC = auto()  # Page container
    CUSTOM = auto()  # Other page indicators


@dataclass
class Dimension:
    """
    Represents a numeric dimension (width/height) plus its unit.
    """

    value: float
    unit: str

    @classmethod
    def from_style_value(cls, value: str) -> "Dimension":
        """
        Parse dimension from style value like '1920px', '595.44pt', '210mm', '29.7cm', '8.5in', etc.
        """
        match = re.match(r"(\d+\.?\d*)(\w+)", value)
        if not match:
            raise ValueError(f"Invalid dimension format: {value}")

        numeric_part = float(match.group(1))
        unit_part = match.group(2).lower()

        # Convert recognized units to px right away for simplification
        if unit_part == "px":
            return cls(numeric_part, "px")
        elif unit_part == "pt":
            # 1pt ~ 1.3333px for 96 DPI but store as 'pt' if you prefer
            return cls(numeric_part, "pt")
        elif unit_part == "mm":
            # 1mm ~ 3.77953px at 96 DPI
            return cls(numeric_part * 3.77953, "px")
        elif unit_part == "cm":
            # 1cm = 10mm => 37.7953px at 96 DPI
            return cls(numeric_part * 37.7953, "px")
        elif unit_part == "in":
            # 1in = 25.4 mm => ~96px at 96 DPI
            return cls(numeric_part * 96.0, "px")
        else:
            raise ValueError(f"Unsupported unit: {unit_part}")

    def to_pixels(self) -> float:
        """
        Convert dimension to pixels if needed.
        If Dimension already stored as px, return directly;
        if pt, approximate conversion; else return the raw value.
        """
        if self.unit == "px":
            return self.value
        elif self.unit == "pt":
            # Commonly 1pt = 1.3333px at 96 DPI
            return self.value * 1.3333
        return self.value


@dataclass
class PageMetrics:
    """
    Holds measurements/properties for a 'page'-like element.
    """

    width: Dimension
    height: Dimension
    orientation: Orientation
    page_number: int
    style_type: StyleType
    page_type: PageType
    container_hierarchy: List[str] = field(default_factory=list)
    style_rules: Dict[str, str] = field(default_factory=dict)
    tag_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def aspect_ratio(self) -> float:
        """Return width/height in px as a float."""
        h_px = self.height.to_pixels()
        # Avoid division by zero
        return self.width.to_pixels() / h_px if h_px else 0.0

    @property
    def is_landscape(self) -> bool:
        """Heuristic to check if the page is in landscape orientation."""
        return self.aspect_ratio > 1.0


class XHTMLAnalyzer:
    """
    Analyzer that scans an XHTML document to detect pseudo-'pages' (divs or blocks with .pf, .pc,
    or large dimension styles), gather style info, orientation, and XBRL presence.
    """

    def __init__(self):
        self.pages: List[PageMetrics] = []
        self.tag_structure: Dict[str, int] = {}
        self.style_info: Dict[str, Dict] = {
            "css": {},  # Populated from global <style> blocks
            "inline": {},  # Inline style eventually stored or parsed
        }
        self.has_xbrl: bool = False
        self.tree = None
        self.style_type: Optional[StyleType] = None
        self.processed_elements: Set[ET.Element] = set()

    def analyze_file(self, xhtml_path: Union[str, Path]) -> dict:
        """
        Entry point to parse and analyze the XHTML file at xhtml_path.
        Returns a dictionary of extracted info, suitable for JSON serialization.
        """
        self.tree = ET.parse(str(xhtml_path))
        root = self.tree.getroot()

        # 1) Parse global <style> blocks (CSS) and infer style type
        self._parse_style_blocks(root)
        self.style_type = self._infer_style_type(root)

        # 2) Detect XBRL by scanning namespaces,
        #    plus a fallback check for tags containing "xbrl" in their name.
        self._detect_xbrl(xhtml_path, root)

        # 3) Process pages hierarchically
        page_counter = 1
        self.processed_elements.clear()

        # First pass: Look for explicit page markers (.pf, .pc)
        for element in root.iter():
            if element in self.processed_elements:
                continue

            classes = element.get("class", "").split()
            if "pf" in classes:
                page_type = PageType.PF
            elif "pc" in classes:
                page_type = PageType.PC
            else:
                continue

            width, height, style_rules = self._extract_dimensions(element)
            if width and height:
                pm = self._create_page_metrics(
                    element,
                    width,
                    height,
                    page_number=page_counter,
                    page_type=page_type,
                    style_rules=style_rules,
                )
                self.pages.append(pm)
                self._mark_element_processed(element)
                page_counter += 1

        # Second pass: Look for semantic indicators if no explicit pages found
        if not self.pages:
            for element in root.iter():
                if element in self.processed_elements:
                    continue

                is_page = self._is_semantic_page(element)
                if is_page:
                    width, height, style_rules = self._extract_dimensions(element)
                    if width and height:
                        pm = self._create_page_metrics(
                            element,
                            width,
                            height,
                            page_number=page_counter,
                            page_type=PageType.CUSTOM,
                            style_rules=style_rules,
                        )
                        self.pages.append(pm)
                        self._mark_element_processed(element)
                        page_counter += 1

        return self._generate_report()

    def _mark_element_processed(self, element: ET.Element) -> None:
        """
        Mark an element and all its children as processed to avoid double-counting.
        """
        self.processed_elements.add(element)
        for child in element.iter():
            self.processed_elements.add(child)

    def _is_semantic_page(self, element: ET.Element) -> bool:
        """
        Check if element has semantic indicators of being a page.
        """
        # First check existing semantic indicators
        page_keywords = {"page", "sheet", "pageview", "pdf-page", "print-page"}

        elem_id = element.get("id", "").lower()
        if any(kw in elem_id for kw in page_keywords):
            return True

        classes = element.get("class", "").lower().split()
        if any(kw in cls for kw in page_keywords for cls in classes):
            return True

        style_attr = element.get("style", "").lower()
        print_indicators = {"page-break", "break-after", "break-before", "@page"}
        if any(ind in style_attr for ind in print_indicators):
            return True

        # Add dimension-based detection
        width, height, _ = self._extract_dimensions(element)
        if width and height:
            # Check if dimensions are significant enough to be a page
            w_px = width.to_pixels()
            h_px = height.to_pixels()

            # Consider elements with dimensions above certain thresholds as pages
            # These thresholds match common document dimensions
            if w_px >= 500 and h_px >= 500:  # Minimum size threshold
                return True

            # Additional checks for common page sizes (in pixels at 96 DPI)
            common_sizes = [
                (1920, 1080),  # HD
                (1080, 1528),  # Common in sample 3
                (816, 1056),  # US Letter
                (816, 1144),  # US Legal
                (595, 842),  # A4
            ]

            # Allow for some variation in dimensions (±10%)
            for common_w, common_h in common_sizes:
                w_match = 0.9 * common_w <= w_px <= 1.1 * common_w
                h_match = 0.9 * common_h <= h_px <= 1.1 * common_h
                if (w_match and h_match) or (
                    w_match and h_match
                ):  # Match in either orientation
                    return True

        return False

    def _parse_style_blocks(self, root: ET.Element) -> None:
        """
        Look for <style> blocks in the XHTML and parse them into self.style_info['css'] for
        possible class-based style rules.
        """
        style_elements = root.findall(".//{http://www.w3.org/1999/xhtml}style")
        if not style_elements:
            # Fallback if no namespace used
            style_elements = root.findall(".//style")

        block_pattern = re.compile(r"([^{}]+)\{([^{}]+)\}")

        for style_elem in style_elements:
            style_content = "".join(style_elem.itertext()).strip()
            # Find something like ".page { width:210mm; height:297mm; }"
            for match in block_pattern.finditer(style_content):
                selectors, css_body = match.groups()
                rules_dict = self._parse_style_rules(css_body)
                for sel in selectors.split(","):
                    sel_clean = sel.strip()
                    if sel_clean:
                        self.style_info["css"].setdefault(sel_clean, {}).update(
                            rules_dict
                        )

    def _parse_style_rules(self, style_str: str) -> Dict[str, str]:
        """
        Convert a CSS style string (e.g. 'width:210mm; height:297mm') into a dict.
        """
        rules = {}
        if not style_str:
            return rules

        for rule in style_str.split(";"):
            rule_clean = rule.strip()
            if not rule_clean:
                continue
            if ":" in rule_clean:
                prop, val = rule_clean.split(":", 1)
                rules[prop.strip()] = val.strip()

        return rules

    def _detect_xbrl(self, xhtml_path: Union[str, Path], root: ET.Element) -> None:
        """
        Determine whether the document contains XBRL extension or inline XBRL.
        We do both: check the file's namespaces and scan tags for 'xbrl'.
        """
        # Build namespace dict manually to ensure we store just the URI strings
        namespaces = {}
        for event, (prefix, uri) in ET.iterparse(str(xhtml_path), events=["start-ns"]):
            namespaces[prefix] = uri

        # Check if any namespace URI contains 'xbrl'
        if any("xbrl" in uri.lower() for uri in namespaces.values()):
            self.has_xbrl = True

        # fallback check for any tag containing 'xbrl'
        if not self.has_xbrl:
            if any("xbrl" in (child.tag.lower() or "") for child in root.iter()):
                self.has_xbrl = True

    def _extract_dimensions(
        self, element: ET.Element
    ) -> Tuple[Optional[Dimension], Optional[Dimension], Dict[str, str]]:
        """
        Extract width, height, and style properties from element by combining inline
        style rules with any global CSS classes. Return (width, height, combined_style).
        """
        combined_style = self._get_combined_style_rules(element)

        width = self._parse_dimension(combined_style.get("width"))
        height = self._parse_dimension(combined_style.get("height"))

        # Also fallback to element attributes if style-based dims not found
        if not width and element.get("width"):
            width = self._parse_dimension(element.get("width"))
        if not height and element.get("height"):
            height = self._parse_dimension(element.get("height"))

        return width, height, combined_style

    def _get_combined_style_rules(self, element: ET.Element) -> Dict[str, str]:
        """
        Merge inline style with any matching global CSS rules from <style> blocks
        (by matching classes on the element).
        """
        combined = {}
        classes = element.get("class", "").split()
        for cls_name in classes:
            selector = f".{cls_name}"
            if selector in self.style_info["css"]:
                combined.update(self.style_info["css"][selector])

        inline_rules = self._parse_style_rules(element.get("style", ""))
        combined.update(inline_rules)
        return combined

    def _parse_dimension(self, dim_str: Optional[str]) -> Optional[Dimension]:
        """
        Convert dimension string (e.g. '210mm') to Dimension object, if valid.
        """
        if not dim_str:
            return None
        try:
            return Dimension.from_style_value(dim_str)
        except ValueError:
            return None

    def _infer_style_type(self, root: ET.Element) -> Optional[StyleType]:
        """
        Infer the document's style type based on the presence of style attributes,
        stylesheet links, and style tags.
        """
        style_attrs = sum(1 for elem in root.iter() if elem.attrib.get("style"))
        link_tags = any(
            child.tag.lower() == "link"
            and "stylesheet" in (child.attrib.get("rel", "").lower())
            for child in root.iter()
        )
        style_tags = any(child.tag.lower() == "style" for child in root.iter())

        if style_attrs > 10 and (link_tags or style_tags):
            return StyleType.MIXED
        elif style_attrs > 10:
            return StyleType.INLINE
        elif link_tags or style_tags:
            return StyleType.CSS
        return None

    def _create_page_metrics(
        self,
        element: ET.Element,
        width: Dimension,
        height: Dimension,
        page_number: int,
        page_type: PageType,
        style_rules: Dict[str, str],
    ) -> PageMetrics:
        """
        Build a PageMetrics object from extracted data.
        """
        orientation = (
            Orientation.LANDSCAPE
            if width.to_pixels() >= height.to_pixels()
            else Orientation.PORTRAIT
        )

        # Use the document's overall style type if available, otherwise fallback to element-specific
        used_style_type = self.style_type or (
            StyleType.INLINE if element.get("style") else StyleType.CSS
        )

        metrics = PageMetrics(
            width=width,
            height=height,
            orientation=orientation,
            page_number=page_number,
            style_type=used_style_type,
            page_type=page_type,
            container_hierarchy=self._get_container_hierarchy(element),
            style_rules=style_rules,
        )

        # Count tags recursively in this element's subtree
        tag_counts = {}
        self._count_tags(element, tag_counts)
        metrics.tag_counts = tag_counts

        return metrics

    def _get_container_hierarchy(self, element: ET.Element) -> List[str]:
        """
        Build a small 'parent -> child -> grandchild' style path from root to the given element,
        including class if available.
        """
        hierarchy = []
        current = element

        while current is not None:
            if isinstance(current.tag, str):
                tag_name = current.tag.split("}")[-1]  # remove namespace if any
                class_attr = current.get("class", "").replace(" ", ".")
                hierarchy.insert(
                    0, f"{tag_name}{f'.{class_attr}' if class_attr else ''}"
                )

            # Find parent by searching from root
            found_parent = None
            if self.tree is not None:
                root = self.tree.getroot()
                for possible_parent in root.iter():
                    for child in possible_parent:
                        if child == current:
                            found_parent = possible_parent
                            break
                    if found_parent is not None:
                        break
            current = found_parent

        return hierarchy

    def _count_tags(self, element: ET.Element, counts: Dict[str, int]) -> None:
        """
        Recursively count tags in the subtree for the given element.
        """
        if isinstance(element.tag, str):
            tag = element.tag
            counts[tag] = counts.get(tag, 0) + 1
        for child in element:
            self._count_tags(child, counts)

    def _generate_report(self) -> dict:
        """
        Package up all results (PageMetrics) into a top-level dictionary
        suitable for JSON serialization or further processing.
        """
        return {
            "document_info": {
                "total_pages": len(self.pages),
                "style_type": self.style_type.name if self.style_type else None,
                "has_xbrl": self.has_xbrl,
            },
            "pages": [
                {
                    "number": pm.page_number,
                    "type": pm.page_type.name.lower(),
                    "dimensions": {
                        "width": f"{pm.width.value}{pm.width.unit}",
                        "height": f"{pm.height.value}{pm.height.unit}",
                        "orientation": pm.orientation.name.lower(),
                    },
                    "content": {
                        "tag_counts": pm.tag_counts,
                        "container_hierarchy": pm.container_hierarchy,
                        "style_rules": pm.style_rules,
                    },
                }
                for pm in self.pages
            ],
        }


def analyze_xhtml(file_path: Union[str, Path]) -> dict:
    """
    Convenience wrapper to instantiate XHTMLAnalyzer and get a dictionary report.
    """
    analyzer = XHTMLAnalyzer()
    return analyzer.analyze_file(file_path)


if __name__ == "__main__":
    sample_files = [
        "assets/xhtml/sample_0.xhtml",
        "assets/xhtml/sample_1.xhtml",
        "assets/xhtml/sample_2.xhtml",
        "assets/xhtml/sample_3.xhtml",
        "assets/xhtml/sample_4.xhtml",
        "assets/xhtml/sample_5.xhtml",
    ]
    for file_path in sample_files:
        report = analyze_xhtml(file_path)
        # Save the report to JSON
        with open(
            f"assets/xhtml/report_{Path(file_path).name}.json", "w", encoding="utf-8"
        ) as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
