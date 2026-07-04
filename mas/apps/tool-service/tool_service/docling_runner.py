"""Isolated Docling conversion helper used by ``document.ingest``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docling.document_converter import DocumentConverter


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m tool_service.docling_runner <document>")
    source = Path(sys.argv[1]).resolve(strict=True)
    document = DocumentConverter().convert(source).document
    payload = {
        "document": document.export_to_dict(),
        "text": document.export_to_markdown(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
