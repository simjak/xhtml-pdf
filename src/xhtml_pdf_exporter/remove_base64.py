import argparse
import base64
import re
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image


def resize_base64_image(base64_str: str, reduce_percent: int) -> Optional[str]:
    """Resize a base64 image by the given percentage."""
    try:
        # Extract the image format and data
        if "," in base64_str:
            format_data, base64_data = base64_str.split(",", 1)
            mime_type = format_data.split(";")[0].split(":")[1]
            img_format = mime_type.split("/")[-1].upper()
        else:
            return None

        # Decode base64 to image
        img_data = base64.b64decode(base64_data)
        img = Image.open(BytesIO(img_data))

        # Calculate new size
        new_width = int(img.width * (100 - reduce_percent) / 100)
        new_height = int(img.height * (100 - reduce_percent) / 100)

        # Resize image
        resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Save resized image to bytes
        output_buffer = BytesIO()
        resized_img.save(output_buffer, format=img_format)
        resized_bytes = output_buffer.getvalue()

        # Encode back to base64
        resized_base64 = base64.b64encode(resized_bytes).decode("utf-8")
        return f"{format_data},{resized_base64}"

    except Exception:
        return None


def remove_base64_content(
    content: str, background_only: bool = False, reduce_size: Optional[int] = None
) -> str:
    # Generic data URI pattern for images and fonts
    base64_pattern = (
        r"data:(?:image/[^;,\s]+|"
        r"application/(?:x-)?font-(?:woff|woff2|ttf|otf|eot)|"
        r"font/(?:woff|woff2|ttf|otf|eot)|"
        r"application/(?:x-)?(?:font-)?(?:truetype|opentype));base64,[a-zA-Z0-9+/=]+"
    )

    if reduce_size is not None:
        # Function to handle image resizing
        def resize_match(match: re.Match) -> str:
            resized = resize_base64_image(match.group(2), reduce_size)
            return resized if resized else match.group(2)

        # Resize background images
        content = re.sub(
            r'(background(?:-image)?:\s*url\(["\']?)('
            + base64_pattern
            + r')(["\']?\);?)',
            lambda m: m.group(1) + resize_match(m) + m.group(3),
            content,
        )

        # Resize images in attributes
        attrs = ["src", "srcset", "poster", "href", "data-src"]
        for attr in attrs:
            content = re.sub(
                r"(" + attr + r'=["\']?)(' + base64_pattern + r')(["\']?)',
                lambda m: m.group(1) + resize_match(m) + m.group(3),
                content,
            )

        return content

    # If not resizing, handle removal
    if background_only:
        # Only remove background images
        content = re.sub(
            r'background(?:-image)?:\s*url\(["\']?' + base64_pattern + r'["\']?\);?',
            "",
            content,
        )
        content = re.sub(r'url\(["\']?' + base64_pattern + r'["\']?\)', "", content)
    else:
        # Remove all base64 content
        content = re.sub(
            r'background(?:-image)?:\s*url\(["\']?' + base64_pattern + r'["\']?\);?',
            "",
            content,
        )
        content = re.sub(r'url\(["\']?' + base64_pattern + r'["\']?\)', "", content)

        # Add handling for CSS content property with base64
        content = re.sub(
            r'content:\s*["\']?url\(["\']?' + base64_pattern + r'["\']?\)["\']?;?',
            "",
            content,
        )

        # Handle CSS variables containing base64
        content = re.sub(
            r'--[a-zA-Z0-9-]+:\s*url\(["\']?' + base64_pattern + r'["\']?\);?',
            "",
            content,
        )

        # More thorough font-face removal patterns
        font_face_patterns = [
            # Standard @font-face with url()
            r"@font-face\s*{[^}]*?url\(['\"]?data:[^)]+['\"]?\)[^}]*?}",
            # @font-face with src: format
            r"@font-face\s*{[^}]*?src:\s*url\(['\"]?data:[^)]+['\"]?\)[^}]*?}",
            # Catch any remaining @font-face rules with base64 content
            r"@font-face\s*{[^}]*?" + base64_pattern + r"[^}]*?}",
        ]

        for pattern in font_face_patterns:
            content = re.sub(pattern, "", content, flags=re.IGNORECASE)

        attrs = ["src", "srcset", "poster", "href", "data-src"]
        for attr in attrs:
            # Find img elements with base64 and preserve their dimensions
            content = re.sub(
                r"(<img[^>]*?"
                + attr
                + r'=["\']?)'
                + base64_pattern
                + r'(["\']?[^>]*?>)',
                lambda m: preserve_img_dimensions(m.group(0), m.group(1), m.group(2)),
                content,
            )
            # Handle other elements with base64
            content = re.sub(
                r"(" + attr + r'=["\']?)' + base64_pattern + r'(["\']?)',
                r"\1data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7\2",
                content,
            )

        # For SVG images, preserve dimensions
        content = re.sub(
            r"(<svg[^>]*>).*?(<image[^>]*?" + base64_pattern + r"[^>]*>).*?</svg>",
            lambda m: preserve_svg_dimensions(m.group(0), m.group(1)),
            content,
            flags=re.DOTALL,
        )

        # For background images in style attributes, replace with transparent
        content = re.sub(
            r'(<[^>]*?style=["\']?[^>]*?)url\(["\']?'
            + base64_pattern
            + r'["\']?\)([^>]*?>)',
            r'\1url("data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")\2',
            content,
        )

        # For CSS background images, replace with transparent
        content = re.sub(
            r'(background(?:-image)?:\s*url\(["\']?)'
            + base64_pattern
            + r'(["\']?\);?)',
            r"\1data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7\2",
            content,
        )

    return content


def process_file(
    input_path: str, background_only: bool = False, reduce_size: Optional[int] = None
) -> None:
    try:
        # Convert to Path object
        path = Path(input_path)

        # Read input file
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Process base64 content
        cleaned_content = remove_base64_content(content, background_only, reduce_size)

        # Generate output filename
        if reduce_size is not None:
            suffix = f"_reduced_{reduce_size}"
        else:
            suffix = "_no_background" if background_only else "_no_base64"
        output_path = path.parent / f"{path.stem}{suffix}{path.suffix}"

        # Write output file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(cleaned_content)

        print(f"Successfully processed file. Output saved to: {output_path}")

    except Exception as e:
        print(f"Error processing file: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Process base64 content in XHTML files"
    )
    parser.add_argument("input_file", help="Input XHTML file")
    parser.add_argument(
        "--background-only",
        action="store_true",
        help="Remove only background images, preserve other base64 content",
    )
    parser.add_argument(
        "--reduce-size",
        type=int,
        metavar="PERCENT",
        help="Reduce image sizes by specified percentage (1-99)",
    )

    args = parser.parse_args()

    if args.reduce_size is not None and not (1 <= args.reduce_size <= 99):
        print("Error: --reduce-size must be between 1 and 99", file=sys.stderr)
        sys.exit(1)

    process_file(args.input_file, args.background_only, args.reduce_size)


def preserve_img_dimensions(full_tag: str, prefix: str, suffix: str) -> str:
    """Preserve image dimensions while replacing base64 content."""
    # Extract width and height if present
    width_match = re.search(r'width=["\'"]?(\d+%?)', full_tag)  # Added % support
    height_match = re.search(r'height=["\'"]?(\d+%?)', full_tag)  # Added % support
    style_match = re.search(r'style=["\'](.*?)["\']', full_tag)

    # Build style attribute
    styles = []
    if style_match:
        # Preserve all existing styles, including any dimension-related ones
        existing_styles = style_match.group(1).rstrip(";").split(";")
        styles.extend(s.strip() for s in existing_styles if s.strip())

    # Add dimensions only if they're not already in style
    if width_match and not any(s.startswith("width:") for s in styles):
        width_val = width_match.group(1)
        styles.append(f"width: {width_val}" + ("" if width_val.endswith("%") else "px"))
    if height_match and not any(s.startswith("height:") for s in styles):
        height_val = height_match.group(1)
        styles.append(
            f"height: {height_val}" + ("" if height_val.endswith("%") else "px")
        )

    # Create new tag with preserved dimensions
    style_attr = f' style="{"; ".join(styles)}"' if styles else ""

    # Preserve all other attributes except width, height, and style
    preserved_attrs = re.sub(r'(width|height|style)=["\'][^"\']*["\']', "", full_tag)
    preserved_attrs = re.sub(r"\s+", " ", preserved_attrs).strip()

    return (
        f"{prefix}data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7{suffix}"
        f"{style_attr}"
    )


def preserve_svg_dimensions(svg_content: str, svg_open_tag: str) -> str:
    """Preserve SVG dimensions while replacing content with empty SVG."""
    # Extract width, height, and viewBox
    width_match = re.search(r'width=["\'"]?(\d+%?)', svg_open_tag)  # Added % support
    height_match = re.search(r'height=["\'"]?(\d+%?)', svg_open_tag)  # Added % support
    viewbox_match = re.search(r'viewBox=["\']([\d\s.]+)["\']', svg_open_tag)
    style_match = re.search(r'style=["\'](.*?)["\']', svg_open_tag)

    # Build preserved attributes
    attrs = []
    if width_match:
        width_val = width_match.group(1)
        attrs.append(f'width="{width_val}"')
    if height_match:
        height_val = height_match.group(1)
        attrs.append(f'height="{height_val}"')
    if viewbox_match:
        attrs.append(f'viewBox="{viewbox_match.group(1)}"')
    if style_match:
        attrs.append(f'style="{style_match.group(1)}"')

    # Create empty SVG with preserved dimensions
    return f'<svg xmlns="http://www.w3.org/2000/svg" {" ".join(attrs)}></svg>'


if __name__ == "__main__":
    main()
