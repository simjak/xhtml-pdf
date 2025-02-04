import json
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
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
    element: ET.Element
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
        self.debug: bool = False

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
        """Enhanced page detection logic."""
        # Existing code...

        # Add checks for common page indicators
        if element.tag.endswith("}div"):  # Handle namespaced tags
            classes = element.get("class", "").split()

            # Common page class patterns
            page_classes = {"pf", "pc", "pageView", "page", "page-container"}
            if any(cls in classes for cls in page_classes):
                return True

            # Check for size-based indicators
            style = element.get("style", "")
            if "width" in style and "height" in style:
                width_match = re.search(r"width:\s*(\d+\.?\d*)(pt|px|mm|cm|in)", style)
                height_match = re.search(
                    r"height:\s*(\d+\.?\d*)(pt|px|mm|cm|in)", style
                )
                if width_match and height_match:
                    # Convert to points for comparison
                    width_val = self._convert_to_points(
                        float(width_match.group(1)), width_match.group(2)
                    )
                    height_val = self._convert_to_points(
                        float(height_match.group(1)), height_match.group(2)
                    )

                    # Check if dimensions suggest a page
                    return (
                        width_val > 400 and height_val > 400
                    )  # Typical page size threshold

            # Check for page-specific attributes
            page_indicators = {"data-page", "data-page-number", "page-number"}
            if any(attr in element.attrib for attr in page_indicators):
                return True

        return False

    def _convert_to_points(self, value: float, unit: str) -> float:
        """Convert various units to points."""
        conversions = {
            "pt": 1,
            "px": 0.75,  # 1px ≈ 0.75pt
            "mm": 2.83465,  # 1mm ≈ 2.83465pt
            "cm": 28.3465,  # 1cm = 10mm
            "in": 72,  # 1in = 72pt
        }
        return value * conversions.get(unit, 1)

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
            element=element,
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

    def _extract_page_numbers(self, element: ET.Element) -> Dict[str, Optional[str]]:
        """Enhanced page number extraction with hierarchical analysis."""
        numbers = {
            "physical": None,
            "document": None,
            "section": None,
            "context": {},  # Store additional context
        }

        if self.debug:
            print("\nPage Number Analysis:")
            print(f"Element ID: {element.get('id', 'No ID')}")

        # 1. Check structural indicators
        parent_chain = []
        current = element
        while current is not None:
            if isinstance(current.tag, str):
                attrs = current.attrib
                parent_chain.append(
                    {
                        "tag": current.tag,
                        "id": attrs.get("id", ""),
                        "class": attrs.get("class", ""),
                        "data-page": attrs.get("data-page", ""),
                    }
                )
                # Look for page indicators in each ancestor
                for attr, value in attrs.items():
                    if any(x in attr.lower() for x in ["page", "num", "index"]):
                        numbers["context"][f"ancestor_{attr}"] = value
            current = current.getparent() if hasattr(current, "getparent") else None

        if self.debug:
            print("Parent chain:", parent_chain)

        # 2. Check explicit page attributes with enhanced patterns
        page_patterns = {
            "id": [
                r"pf(\d+)",
                r"page[_-]?(\d+)",
                r"p(\d+)",
            ],
            "data-page": [r"(\d+)"],
            "data-page-number": [r"(\d+)"],
            "data-document-page": [r"(\d+)"],
        }

        for attr, patterns in page_patterns.items():
            value = element.get(attr)
            if value:
                for pattern in patterns:
                    match = re.search(pattern, value.lower())
                    if match:
                        numbers["document"] = match.group(1)
                        numbers["context"][f"from_{attr}"] = value
                        if self.debug:
                            print(f"Found number in {attr}: {match.group(1)}")
                        break

        # 3. Analyze text content with context
        text = "".join(element.itertext()).strip()
        text_patterns = [
            (r"Page\s*(\d+)\s*of\s*(\d+)", "page_of_total"),
            (r"[Pp]age\s*(\d+)", "page_label"),
            (r"^\s*(\d+)\s*$", "standalone_number"),
            (r"\b(\d+)\s*of\s*\d+\b", "x_of_y"),
            (r"§\s*(\d+)", "section_number"),
        ]

        for pattern, pattern_type in text_patterns:
            match = re.search(pattern, text)
            if match:
                numbers["document"] = match.group(1)
                if pattern_type == "page_of_total" and len(match.groups()) > 1:
                    numbers["context"]["total_pages"] = match.group(2)
                numbers["context"]["pattern_type"] = pattern_type
                if self.debug:
                    print(f"Found {pattern_type}: {match.group(1)}")
                break

        # 4. Check XBRL elements for page numbers
        ns = "{http://www.xbrl.org/2013/inlineXBRL}"
        for xbrl_elem in element.findall(f".//{ns}*"):
            name = xbrl_elem.get("name", "")
            if any(x in name.lower() for x in ["page", "num", "index"]):
                numbers["context"]["xbrl_page_ref"] = name
                if self.debug:
                    print(f"Found XBRL page reference: {name}")

        # 5. Validate and clean numbers
        if numbers["document"]:
            try:
                # Ensure numeric and remove leading zeros
                numbers["document"] = str(int(numbers["document"]))
            except ValueError:
                numbers["document"] = None

        if self.debug:
            print("Final numbers:", numbers)
            print("Text context:", text[:100] if len(text) > 100 else text)

        return numbers

    def _analyze_page_structure(self, element: ET.Element) -> Dict[str, Any]:
        """Enhanced page structure analysis with better logging."""
        structure = {
            "depth": 0,
            "child_count": len(list(element)),
            "text_blocks": len(list(element.itertext())),
            "numbers": self._extract_page_numbers(element),
            "classes": element.get("class", "").split(),
            "attributes": dict(element.attrib),
            "parent_chain": [],
            "xbrl_elements": [],
            "style_info": {},
        }

        # Log detailed structure
        if self.debug:
            print("\nPage Structure Analysis:")
            print(f"Element tag: {element.tag}")
            print(f"Classes: {structure['classes']}")
            print(f"Attributes: {structure['attributes']}")
            print(f"Child count: {structure['child_count']}")

            # Analyze parent chain
            parent = element
            while parent is not None:
                structure["parent_chain"].append(
                    {"tag": parent.tag, "class": parent.get("class", "")}
                )
                parent = parent.getparent() if hasattr(parent, "getparent") else None

            print("Parent chain:", structure["parent_chain"])

            # Log style information
            style = element.get("style", "")
            if style:
                style_dict = dict(
                    item.split(":") for item in style.split(";") if ":" in item
                )
                structure["style_info"] = style_dict
                print("Style info:", style_dict)

            # Check for XBRL elements
            ns = "{http://www.xbrl.org/2013/inlineXBRL}"
            xbrl_elements = element.findall(f".//{ns}*")
            if xbrl_elements:
                print(f"Found {len(xbrl_elements)} XBRL elements")
                structure["xbrl_elements"] = [
                    e.tag.replace(ns, "") for e in xbrl_elements
                ]

        return structure

    def _generate_report(self) -> dict:
        """
        Package up all results (PageMetrics) into a top-level dictionary
        suitable for JSON serialization or further processing.
        """
        report = {
            "document_info": {
                "total_pages": len(self.pages),
                "style_type": self.style_type.name if self.style_type else None,
                "has_xbrl": self.has_xbrl,
                "page_number_format": self._detect_page_number_format(),
                "structure_summary": self._generate_structure_summary(),
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
                        "structure": self._analyze_page_structure(pm.element),
                    },
                }
                for pm in self.pages
            ],
        }
        return report

    def _detect_page_number_format(self) -> Dict[str, Any]:
        """Analyze page numbering patterns in the document."""
        return {
            "physical_to_document": {
                pm.page_number: self._extract_page_numbers(pm.element)
                for pm in self.pages
            },
            "number_gaps": self._find_page_number_gaps(),
            "numbering_type": self._detect_numbering_type(),
        }

    def _find_page_number_gaps(self) -> List[int]:
        """Find gaps in page number sequence."""
        page_numbers = sorted(pm.page_number for pm in self.pages)
        expected = set(range(min(page_numbers), max(page_numbers) + 1))
        return sorted(expected - set(page_numbers))

    def _detect_numbering_type(self) -> str:
        """Detect the type of page numbering used."""
        # Analyze patterns in page numbers to determine if they're:
        # - Sequential (1,2,3...)
        # - Section-based (1.1, 1.2...)
        # - Document-based (222, 223...)
        return "mixed" if self._has_multiple_numbering_systems() else "sequential"

    def _has_multiple_numbering_systems(self) -> bool:
        """Check if document uses multiple numbering systems."""
        physical_numbers = {pm.page_number for pm in self.pages}
        document_numbers = {
            int(num)
            for pm in self.pages
            if (num := self._extract_page_numbers(pm.element)["document"])
            and num.isdigit()
        }
        return bool(document_numbers) and physical_numbers != document_numbers

    def _generate_structure_summary(self) -> Dict[str, Any]:
        """Generate a summary of the document's structure."""
        # This is a placeholder implementation. You might want to implement
        # a more robust structure analysis based on your document's structure.
        return {
            "total_elements": len(self.processed_elements),
            "total_pages": len(self.pages),
            "style_type": self.style_type.name if self.style_type else None,
            "has_xbrl": self.has_xbrl,
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
        # "assets/xhtml/sample_1.xhtml",
        # "assets/xhtml/sample_2.xhtml",
        "assets/xhtml/sample_3.xhtml",
        "assets/xhtml/sample_4.xhtml",
        # "assets/xhtml/sample_5.xhtml",
        # "assets/xhtml/sample_7.xhtml",
        # "assets/xhtml/sample_6.html",
    ]
    for file_path in sample_files:
        report = analyze_xhtml(file_path)
        # Save the report to JSON
        with open(
            f"assets/xhtml/report_{Path(file_path).name}.json", "w", encoding="utf-8"
        ) as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
