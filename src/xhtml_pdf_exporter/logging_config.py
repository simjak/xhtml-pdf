import logging
import sys

def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging with a standard format."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)

    # Add handler to logger
    root_logger.addHandler(handler)
