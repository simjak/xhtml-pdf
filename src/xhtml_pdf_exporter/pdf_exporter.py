import os
import tempfile
from pathlib import Path
from typing import List, Tuple, Dict, Any
from playwright.sync_api import sync_playwright, Page
from PyPDF2 import PdfMerger

class PDFExporter:
    def __init__(self, input_file: str):
        """Initialize PDF exporter with input XHTML file path."""
        self.input_file = Path(input_file)
        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

    def export_to_pdf(self, output_file: str) -> None:
        """Export XHTML to PDF with dynamic page orientation detection."""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            # Load XHTML file
            page.goto(f"file://{self.input_file.absolute()}")

            # Wait for content to load
            page.wait_for_load_state("networkidle")

            # Get page dimensions and inject CSS
            dimensions = self._get_page_dimensions(page)
            self._inject_orientation_css(page)
            self._apply_orientation_classes(page, dimensions)

            # Create temporary directory for individual PDFs
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_files = []

                # Generate PDF for each page with correct orientation
                for i, (width, height) in enumerate(dimensions):
                    # Configure PDF options for this page
                    pdf_options = self._configure_pdf_options([(width, height)])

                    # Hide all pages except current one
                    page.evaluate("""(index) => {
                        document.querySelectorAll('div.pageView').forEach((el, i) => {
                            el.style.display = i === index ? 'block' : 'none';
                        });
                    }""", i)

                    # Generate PDF for this page
                    temp_pdf = os.path.join(temp_dir, f"page_{i}.pdf")
                    page.pdf(path=temp_pdf, **pdf_options)
                    pdf_files.append(temp_pdf)

                # Merge PDFs
                merger = PdfMerger()
                for pdf_file in pdf_files:
                    merger.append(pdf_file)

                merger.write(output_file)
                merger.close()

            browser.close()

    def _get_page_dimensions(self, page: Page) -> List[Tuple[float, float]]:
        """Get dimensions for each page section to determine orientation."""
        # Get all page view divs
        dimensions = page.eval_on_selector_all("div.pageView", """
            elements => elements.map(el => {
                const rect = el.getBoundingClientRect();
                return [rect.width, rect.height];
            })
        """)
        return dimensions

    def _inject_orientation_css(self, page: Page) -> None:
        """Inject CSS for handling different page orientations."""
        page.add_style_tag(content="""
            @page {
                size: A4 portrait;
                margin: 0;
            }
            @page landscape {
                size: A4 landscape;
                margin: 0;
            }
            div.pageView {
                margin-bottom: 20px;
            }
            div.pageView.landscape {
                page: landscape;
            }
        """)

    def _apply_orientation_classes(self, page: Page, dimensions: List[Tuple[float, float]]) -> None:
        """Apply orientation classes to page divs based on dimensions."""
        page.evaluate("""(dimensions) => {
            document.querySelectorAll('div.pageView').forEach((el, i) => {
                const [width, height] = dimensions[i];
                if (width > height) {
                    el.classList.add('landscape');
                }
            });
        }""", dimensions)

    def _configure_pdf_options(self, dimensions: List[Tuple[float, float]]) -> Dict[str, Any]:
        """Configure PDF options based on page dimensions."""
        width, height = dimensions[0]
        is_landscape = width > height

        return {
            "print_background": True,
            "prefer_css_page_size": True,
            "format": "A4",
            "landscape": is_landscape,
        }

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Export XHTML to PDF with dynamic orientation")
    parser.add_argument("input_file", help="Input XHTML file path")
    parser.add_argument("output_file", help="Output PDF file path")

    args = parser.parse_args()

    exporter = PDFExporter(args.input_file)
    exporter.export_to_pdf(args.output_file)

if __name__ == "__main__":
    main()
