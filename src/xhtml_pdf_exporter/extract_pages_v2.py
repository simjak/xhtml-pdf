import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Union

from bs4 import BeautifulSoup, NavigableString, Tag

logger = logging.getLogger(__name__)


class BulletProofPageExtractor:
    """
    A 'bullet-proof' style iXBRL/ESEF page extractor that covers:
     1) Class-based, style-based, and ID-based markers (pf\\d+, page-break style, etc.).
     2) Splits the document so that each 'page' includes any siblings following the marker,
        until the next marker or end, but does NOT require markers to be direct children of <body>.
     3) Handles duplicates (multiple 'pf1' or 'page1') by not merging them into one file
        unless you choose to do so yourself.
     4) If no markers exist at all, falls back to exporting the entire document as a single file.
    """

    ###########################################################################
    # 1. Configurable patterns for classes, styles, and IDs
    ###########################################################################
    def __init__(self):
        # You can tweak or add patterns for your situation
        self.class_patterns = [
            {"class": "pf"},
            {"class": "page"},
            {"class": "pageView"},
            {"class": "pdf-page"},
        ]

        # Style-based markers: e.g. page-break-before, break-after, etc.
        # We'll treat them as a set of checks on tag.get("style").
        self.style_checks: Dict[str, Callable[[str], bool]] = {
            "page-break-before": lambda style: "page-break-before" in style,
            "page-break-after": lambda style: "page-break-after" in style,
            "break-before": lambda style: "break-before" in style,
            "break-after": lambda style: "break-after" in style,
        }

        # ID-based markers (regex).  For example:  <div id="pf1" ...>
        self.id_patterns = [
            re.compile(r"pf\d+"),
            re.compile(r"page\d+"),
            re.compile(r"pg\d+"),
        ]

    ###########################################################################
    # 2. DOM search utilities
    ###########################################################################
    def _is_style_match(self, tag: Tag, style_check: Callable[[str], bool]) -> bool:
        if not isinstance(tag, Tag):
            return False
        style_attr = tag.get("style", "")
        if not isinstance(style_attr, str):
            return False
        return style_check(style_attr)

    def _make_id_predicate(self, pattern: re.Pattern) -> Callable[[Any], bool]:
        def predicate(tag: Any) -> bool:
            if not isinstance(tag, Tag):
                return False
            tid = tag.get("id", "")
            return bool(pattern.search(tid))

        return predicate

    def _find_by_class(self, soup: BeautifulSoup, pattern: Dict[str, str]) -> List[Tag]:
        # Example: soup.find_all("div", pattern) where pattern might be {"class": "pf"}
        return soup.find_all("div", pattern)

    def _find_by_style(
        self, soup: BeautifulSoup, style_check: Callable[[str], bool]
    ) -> List[Tag]:
        return soup.find_all(lambda t: self._is_style_match(t, style_check))

    def _find_by_id_pattern(
        self, soup: BeautifulSoup, pattern: re.Pattern
    ) -> List[Tag]:
        return soup.find_all(self._make_id_predicate(pattern))

    ###########################################################################
    # 3. Page marker detection – gather *all* markers in DOM order
    ###########################################################################
    def _gather_all_page_markers(self, soup: BeautifulSoup) -> List[Tag]:
        """
        Return *all* possible "page markers" in the entire document
        (not necessarily direct children of <body>).
        """
        results: List[Tag] = []

        # class-based
        for cpat in self.class_patterns:
            results.extend(self._find_by_class(soup, cpat))

        # style-based
        for _, check in self.style_checks.items():
            results.extend(self._find_by_style(soup, check))

        # id-based
        for ipat in self.id_patterns:
            results.extend(self._find_by_id_pattern(soup, ipat))

        return list(set(results))  # remove duplicates if any exact same Tag

    ###########################################################################
    # 4. Attempt to extract a page number from a Tag
    ###########################################################################
    def _extract_page_num(self, tag: Tag, fallback: int) -> int:
        # By default returns fallback if we can't find a numeric portion.
        # 1) Try ID-based
        tid = tag.get("id", "")
        match = re.search(r"(?:pf|page|pg)(\d+)", tid)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass  # fallback

        # 2) Try data-page, data-page-number, etc.
        for a in ["data-page-number", "data-page", "page-number"]:
            val = tag.get(a)
            if val and val.isdigit():
                return int(val)

        # 3) Try class-based
        classes = tag.get("class", [])
        if isinstance(classes, list):
            for c in classes:
                match_c = re.search(r"(?:pf|page|pg)(\d+)", c)
                if match_c:
                    try:
                        return int(match_c.group(1))
                    except ValueError:
                        pass

        # fallback
        return fallback

    ###########################################################################
    # 5. Flatten DOM order to handle slicing
    ###########################################################################
    def _flatten_dom(self, root: Tag) -> List[Union[Tag, NavigableString]]:
        """
        Perform a depth-first traversal to produce a linear list of every node
        (Tag or NavigableString) under 'root'.
        We include the root itself as the first item, unless it is <body> or <html>.
        """
        result: List[Union[Tag, NavigableString]] = []

        def dfs(node: Union[Tag, NavigableString]) -> None:
            result.append(node)
            if isinstance(node, Tag):
                for child in node.contents:
                    dfs(child)

        # If root is body/html, we typically skip listing it as a page node
        # so that we don't create big slices starting from <body> itself.
        # Instead, we just flatten the contents of <body>.
        for c in root.contents:
            dfs(c)

        return result

    ###########################################################################
    # 6. MASTER logic: split the DOM from marker to marker
    ###########################################################################
    def split_document_into_pages(self, soup: BeautifulSoup) -> List[Tuple[int, Tag]]:
        """
        1) Gather all page markers across entire DOM.
        2) Flatten the <body> (or entire soup if no body).
        3) For each marker, find its index in the flattened list.
        4) Sort those indices in ascending order.
        5) Slice from marker i to marker i+1 as a "page."
        """
        markers = self._gather_all_page_markers(soup)
        if not markers:
            logger.warning("No page markers found at all.")
            return []

        body = soup.find("body")
        if not body:
            # fallback to entire soup
            body = soup

        flattened = self._flatten_dom(body)  # entire subtree in order

        # Build (page_num, marker, index_in_flattened)
        marker_info = []
        for i, tag in enumerate(markers):
            # fallback number is i+1
            pg_num = self._extract_page_num(tag, i + 1)
            try:
                idx = flattened.index(tag)  # find the marker in the flattened list
            except ValueError:
                # marker wasn't found in the flattened structure for some reason
                continue
            marker_info.append((pg_num, tag, idx))

        if not marker_info:
            logger.warning("Markers found, but none are present in the flattened DOM.")
            return []

        # Sort by the index in the flattened DOM, then by page number
        marker_info.sort(key=lambda x: (x[2], x[0]))

        # Now create slices
        results = []
        for i in range(len(marker_info)):
            page_num, _, start_idx = marker_info[i]
            if i < len(marker_info) - 1:
                # up to (but not including) next marker
                next_idx = marker_info[i + 1][2]
            else:
                # last marker goes to end
                next_idx = len(flattened)

            # Make a container to hold the slice
            container = soup.new_tag("div", attrs={"class": f"page_{page_num}"})
            # Collate from start_idx to next_idx
            for node in flattened[start_idx:next_idx]:
                # We must "extract" or "copy" these nodes to container
                # If we simply append them, we remove them from their old parent
                container.append(node.extract() if isinstance(node, Tag) else node)

            results.append((page_num, container))

        return results

    ###########################################################################
    # 7. Fallback if no slicing is possible
    ###########################################################################
    def fallback_entire_document(self, soup: BeautifulSoup) -> List[Tuple[int, Tag]]:
        """
        If no markers exist or slicing fails, just return the entire doc as page 1.
        """
        container = soup.new_tag("div", attrs={"class": "fallback_page_1"})
        body_tag = soup.find("body")
        if body_tag:
            # move everything from body into container
            for c in list(body_tag.children):
                container.append(c.extract() if isinstance(c, Tag) else c)
        else:
            container.append(soup)
        return [(1, container)]

    ###########################################################################
    # 8. Public extraction method
    ###########################################################################
    def extract_pages(self, xhtml_path: str) -> List[str]:
        """
        1) Parse content
        2) Attempt a DOM-flattened split by markers
        3) If none, fallback to single page
        4) Write each 'page' to a separate file
        """
        from bs4 import BeautifulSoup

        try:
            text = Path(xhtml_path).read_text(encoding="utf-8")
            soup = BeautifulSoup(text, "lxml-xml")

            # Actually do the slicing
            pages = self.split_document_into_pages(soup)
            if not pages:
                # fallback
                pages = self.fallback_entire_document(soup)

            # Extract <head> if present
            head_content = self._extract_head_content(soup)
            namespaces = self._get_namespaces(soup)

            # Write results
            output_files = []
            for seq, (pg_num, page_tag) in enumerate(pages, start=1):
                output_path = self._create_output_path_with_index(
                    xhtml_path, page_num=pg_num, seq=seq
                )
                page_html = self._create_page_xhtml(
                    head_content=head_content,
                    page_content=str(page_tag),
                    namespaces=namespaces,
                )
                with open(output_path, "w", encoding="utf-8") as out_f:
                    out_f.write(page_html)
                output_files.append(output_path)
                logger.info(
                    f"Extracted page_num={pg_num}, slice #{seq} -> {output_path}"
                )

            return output_files

        except Exception as e:
            logger.error(f"Error extracting pages from {xhtml_path}: {str(e)}")
            return []

    ###########################################################################
    # 9. Helper to extract <head>
    ###########################################################################
    def _extract_head_content(self, soup: BeautifulSoup) -> str:
        head = soup.find("head")
        if head:
            return str(head)
        return ""

    ###########################################################################
    # 10. Helper to extract HTML namespaces from <html>
    ###########################################################################
    def _get_namespaces(self, soup: BeautifulSoup) -> str:
        html_tag = soup.find("html")
        if html_tag:
            # gather all attrs that start with "xmlns" or "xml:lang"
            attrs = []
            for k, v in html_tag.attrs.items():
                if k.startswith("xmlns") or k.startswith("xml:"):
                    attrs.append(f'{k}="{v}"')
            return " ".join(attrs)
        return ""

    ###########################################################################
    # 11. Build final page XHTML
    ###########################################################################
    def _create_page_xhtml(
        self, head_content: str, page_content: str, namespaces: str
    ) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html {namespaces}>
{head_content}
<body>
{page_content}
</body>
</html>"""

    ###########################################################################
    # 12. Create a path that includes both page number and sequence
    ###########################################################################
    def _create_output_path_with_index(
        self, input_path: str, page_num: int, seq: int
    ) -> str:
        """
        So if page_num=2 and seq=15 => something like page_002_015.xhtml
        """
        p = Path(input_path)
        out_dir = p.parent / "extracted_pages"
        out_dir.mkdir(exist_ok=True)
        report_dir = out_dir / p.stem
        report_dir.mkdir(exist_ok=True)
        filename = f"page_{page_num:03d}_{seq:03d}{p.suffix}"
        return str(report_dir / filename)


def main():
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python extract_pages_v2.py <path_to_xhtml>")
        sys.exit(1)

    xhtml_path = sys.argv[1]
    extractor = BulletProofPageExtractor()
    logger.info(f"Processing {xhtml_path}")
    out_files = extractor.extract_pages(xhtml_path)
    logger.info(f"Created {len(out_files)} extracted files.")


if __name__ == "__main__":
    main()
