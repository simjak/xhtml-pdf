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
    # Generic data URI pattern for images
    base64_pattern = r"data:image/[^;,\s]+;base64,[a-zA-Z0-9+/=]+"

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
        content = re.sub(
            r"@font-face\s*{[^}]*" + base64_pattern + r"[^}]*}", "", content
        )

        attrs = ["src", "srcset", "poster", "href", "data-src"]
        for attr in attrs:
            content = re.sub(
                r"<[^>]+" + attr + r'=["\']?' + base64_pattern + r'["\']?[^>]*>',
                "",
                content,
            )

        content = re.sub(
            r"<svg[^>]*>(?:[^<]*|<(?!svg)[^>]*>)*<image[^>]*"
            + base64_pattern
            + r"[^>]*>(?:[^<]*|<(?!svg)[^>]*>)*</svg>",
            "",
            content,
        )

        content = re.sub(
            r'data-[a-zA-Z0-9\-]+=["\']?' + base64_pattern + r'["\']?', "", content
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


if __name__ == "__main__":
    main()
