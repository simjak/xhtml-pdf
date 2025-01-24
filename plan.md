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

1. **Too Much Complexity**
   - Relies on specific HTML structures
   - Tries to handle different tool outputs
   - Complex CSS manipulation
   - Fragile page detection

2. **Unreliable Orientation**
   - Different tools use different approaches
   - CSS classes aren't consistent
   - Page containers vary
   - Structure assumptions break

## Simplified Solution

### 1. Core Orientation Detection

```python
def detect_orientation(self, page: Page) -> bool:
    """Detect if content is landscape by checking rendered dimensions.
    Returns True if landscape, False if portrait."""

    # Get full document dimensions as rendered
    width = page.evaluate("document.documentElement.scrollWidth")
    height = page.evaluate("document.documentElement.scrollHeight")

    # Simple width > height comparison
    return width > height
```

**Benefits:**
- Works with any XHTML structure
- No assumptions about HTML patterns
- Pure dimension-based decision
- Reliable across all tools

### 2. Simplified Export

```python
def export(self, output_path: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Load and render
        page.goto(f"file://{self.input_file.absolute()}")
        page.wait_for_load_state("networkidle")

        # Let browser handle everything except orientation
        page.pdf(
            path=output_path,
            print_background=True,
            prefer_css_page_size=True,
            landscape=self.detect_orientation(page)
        )

        browser.close()
```

**Benefits:**
- Minimal code
- No CSS injection
- Browser handles rendering
- Just one decision: landscape or portrait

## Implementation Strategy

1. **Simplify Code**
   - Remove pattern detection
   - Remove CSS manipulation
   - Focus on orientation only
   - Let browser handle the rest

2. **Testing**
   - Test with various XHTML sources
   - Verify orientation detection
   - Check PDF output quality

## Expected Outcomes

1. **Reliability**
   - Works with any XHTML
   - Correct orientation
   - Consistent output
   - Tool-agnostic

2. **Simplicity**
   - Minimal code
   - Easy to maintain
   - Clear logic
   - No assumptions

3. **Future-Proof**
   - Works with new tools
   - No structure dependencies
   - Easy to understand
   - Easy to modify if needed
