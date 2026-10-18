"""Shared CSV decoding / delimiter-sniffing / row parsing."""

import csv
import io


def parse_csv(raw: bytes) -> tuple[list[dict], list[str]]:
    """Decode bytes → detect delimiter → parse rows into dicts."""
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Could not decode file — unsupported encoding")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # fallback: comma

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("CSV has no header row")

    columns = [str(c).strip() for c in reader.fieldnames if c is not None]
    rows = []
    for row in reader:
        cleaned = {
            str(k).strip(): (str(v).strip() if v is not None else "")
            for k, v in row.items()
            if k is not None
        }
        if any(cleaned.values()):  # skip fully empty rows
            rows.append(cleaned)

    return rows, columns
