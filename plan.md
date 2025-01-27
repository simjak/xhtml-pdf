# XHTML to PDF Converter Enhancement Plan

## Context

Financial reports in XHTML format come from various sources:
- AMANA XBRL Tagger
- ParsePort
- FinTags ESEF
- Deloitte IRIS Carbon
- pdf2htmlEX
- And others

Each tool produces different HTML structures, making it unreliable to depend on specific HTML patterns or CSS classes.

## Current Issues

1. **Document Size Problems**
   - Some documents are extremely tall (300,000+ pixels)
   - Trying to render as single page fails
   - No proper page breaks
   - Memory issues with large documents

2. **Inconsistent Sizing**
   - Different tools use different dimensions
   - No standard page sizes
   - Content may overflow
   - Scaling issues

## Simplified Solution

### 1. Standard Page Sizing

```python
def get_page_dimensions(self) -> tuple[int, int]:
    """Get standard A4 dimensions in pixels."""
    # A4 at 96 DPI
    return (794, 1123)  # Standard A4 dimensions

def get_content_dimensions(self, page) -> tuple[int, int]:
    """Get actual content dimensions."""
    width = page.evaluate("document.documentElement.scrollWidth")
    height = page.evaluate("document.documentElement.scrollHeight")
    return width, height
```

**Benefits:**
- Consistent page sizes
- Standard A4 dimensions
- Predictable output
- Better printing support

### 2. Content Scaling

```python
def export(self, output_path: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Load and wait for content
        page.goto(f"file://{self.input_file.absolute()}")
        page.wait_for_load_state("networkidle")

        # Get dimensions
        content_width, content_height = self.get_content_dimensions(page)
        page_width, page_height = self.get_page_dimensions()

        # Calculate scale to fit width
        scale = min(1.0, page_width / content_width)

        # Generate PDF with proper sizing
        page.pdf(
            path=output_path,
            scale=scale,
            width=f"{page_width}px",
            height=f"{page_height}px",
            print_background=True,
            prefer_css_page_size=False
        )
```

**Benefits:**
- Content always fits width
- Automatic scaling
- Preserves readability
- Handles any document size

## Implementation Strategy

1. **Core Changes**
   - Use standard page sizes
   - Implement content scaling
   - Remove viewport manipulation
   - Let browser handle pagination

2. **Testing**
   - Test with very large documents
   - Verify page breaks
   - Check scaling quality
   - Ensure readability

## Expected Outcomes

1. **Reliability**
   - Works with any document size
   - Consistent page sizes
   - Proper scaling
   - No memory issues

2. **Quality**
   - Professional output
   - Standard A4 pages
   - Readable content
   - Clean page breaks

3. **Compatibility**
   - Works with any XHTML
   - Handles any content size
   - Standard PDF output
   - Print-friendly results
