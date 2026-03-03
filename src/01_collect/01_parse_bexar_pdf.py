"""
01_parse_bexar_pdf.py
---------------------
Parses Bexar County "Numbered Key Canvass" election PDFs into two clean CSVs:
  - {stem}_summary.csv   : one row per candidate (race-level totals)
  - {stem}_precincts.csv : one row per precinct per race (precinct-level votes)

Usage:
    python 01_parse_bexar_pdf.py <path_to_pdf>

Example:
    python 01_parse_bexar_pdf.py ../../data/raw/may_2009_totals.pdf

Output lands in ../../data/processed/ by default (override with --outdir).

Dependencies:
    pip install pdfplumber
"""

import re
import sys
import csv
import argparse
import pdfplumber
from pathlib import Path
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    col_num: int          # the "01 =", "02 =" key from the legend
    name: str
    total_votes: int = 0
    pct: float = 0.0
    is_meta: bool = False # True for OVER VOTES / UNDER VOTES rows


@dataclass
class RaceResult:
    race_name: str
    election_date: str
    candidates: list = field(default_factory=list)
    precinct_rows: list = field(default_factory=list)
    # precinct_rows: list of dicts {precinct, cand_col_num -> votes}


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text(pdf_path: Path) -> str:
    """Extract all text from PDF, preserving whitespace layout."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=2)
            if text:
                pages.append(text)
    return "\n".join(pages)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Matches the section divider between races
DIVIDER_RE = re.compile(r"={40,}")

# Matches candidate legend lines like:
#   01 = Julian Castro                             42,745   56.23
#   06 = Trish DeBerry-Mejia                       22,031   28.98
#   10 = OVER VOTES                                     1
LEGEND_RE = re.compile(
    r"^\s*(\d{2})\s*=\s*(.+?)\s{2,}([\d,]+)\s+([\d.]+)\s*$"
)
# Variant with no percentage (UNDER VOTES sometimes omits it)
LEGEND_NO_PCT_RE = re.compile(
    r"^\s*(\d{2})\s*=\s*(.+?)\s{2,}([\d,]+)\s*$"
)

# Matches the "VOTE FOR  1" instruction line
VOTE_FOR_RE = re.compile(r"VOTE FOR\s+\d+", re.IGNORECASE)

# Matches a precinct data row, e.g.:
#   1001                                    0     1     0     0     0    13     4    62     1     0     0
PRECINCT_ROW_RE = re.compile(r"^(\d{4})\s+([\d\s.]+)$")

# Matches the race header line, e.g.:
#   MAYOR CITY OF SAN ANTONIO
#   Member of Council, Place 1 CITY OF SAN ANTONIO
RACE_HEADER_RE = re.compile(
    r"^((?:MAYOR|Member of Council|City of San Antonio Bond|PROPOSITION).+?)(?:\s{3,}|$)",
    re.IGNORECASE
)

# Matches the column header row like "01    02    03    04    05"
COL_HEADER_RE = re.compile(r"^\s+(\d{2}(?:\s+\d{2})+)\s*$")

# Matches election date in header
DATE_RE = re.compile(r"((?:January|February|March|April|May|June|July|August|"
                     r"September|October|November|December)\s+\d{1,2},\s+\d{4})")


def clean_name(raw: str) -> str:
    return " ".join(raw.strip().split())


def parse_votes(s: str) -> int:
    return int(s.replace(",", "").strip())


# ---------------------------------------------------------------------------
# Section splitter
# ---------------------------------------------------------------------------

def split_into_sections(text: str) -> list[str]:
    """Split full document text on the ==== dividers."""
    parts = DIVIDER_RE.split(text)
    # Filter out very short fragments (headers, page numbers, etc.)
    return [p.strip() for p in parts if len(p.strip()) > 100]


# ---------------------------------------------------------------------------
# Election date extractor
# ---------------------------------------------------------------------------

def extract_election_date(text: str) -> str:
    m = DATE_RE.search(text)
    return m.group(1) if m else "Unknown"


# ---------------------------------------------------------------------------
# Section parser
# ---------------------------------------------------------------------------

def parse_section(section: str, election_date: str) -> RaceResult | None:
    """
    Parse one race section into a RaceResult.
    Returns None if section doesn't look like a race.
    """
    lines = section.splitlines()

    # ---- 1. Find race name ------------------------------------------------
    race_name = None
    for line in lines[:10]:
        m = RACE_HEADER_RE.match(line.strip())
        if m:
            race_name = clean_name(m.group(1))
            break
    if not race_name:
        # Try looser match: first non-blank line that isn't a number
        for line in lines[:10]:
            stripped = line.strip()
            if stripped and not stripped[0].isdigit() and len(stripped) > 5:
                race_name = clean_name(stripped)
                break
    if not race_name:
        return None

    result = RaceResult(race_name=race_name, election_date=election_date)

    # ---- 2. Parse candidate legend ----------------------------------------
    candidates_by_col = {}   # col_num (int) -> Candidate

    for line in lines:
        m = LEGEND_RE.match(line)
        if m:
            col = int(m.group(1))
            name = clean_name(m.group(2))
            votes = parse_votes(m.group(3))
            pct = float(m.group(4))
            is_meta = name.upper() in ("OVER VOTES", "UNDER VOTES")
            c = Candidate(col_num=col, name=name, total_votes=votes,
                          pct=pct, is_meta=is_meta)
            candidates_by_col[col] = c
            result.candidates.append(c)
            continue

        m2 = LEGEND_NO_PCT_RE.match(line)
        if m2:
            col = int(m2.group(1))
            name = clean_name(m2.group(2))
            # Skip if already captured by LEGEND_RE
            if col in candidates_by_col:
                continue
            votes = parse_votes(m2.group(3))
            is_meta = name.upper() in ("OVER VOTES", "UNDER VOTES")
            c = Candidate(col_num=col, name=name, total_votes=votes,
                          pct=0.0, is_meta=is_meta)
            candidates_by_col[col] = c
            result.candidates.append(c)

    if not candidates_by_col:
        return None

    # ---- 3. Determine active candidate columns (non-meta) -----------------
    active_cols = sorted(
        col for col, c in candidates_by_col.items() if not c.is_meta
    )

    # ---- 4. Parse precinct rows -------------------------------------------
    # We look for lines that start with a 4-digit precinct number
    for line in lines:
        m = PRECINCT_ROW_RE.match(line.strip())
        if not m:
            continue

        precinct = m.group(1)
        # The values field contains numbers and dots (dots = leading zeros)
        raw_vals = m.group(2).strip()

        # Replace ". " patterns used for alignment (e.g. ". 81") -> "81"
        # The PDF uses ". " as a spacer before small numbers
        cleaned = re.sub(r"\.\s+", " ", raw_vals)
        # Also handle trailing dot
        cleaned = re.sub(r"\.\s*$", "", cleaned)

        tokens = cleaned.split()

        # Map tokens positionally to all columns (including meta cols like
        # OVER/UNDER VOTES) in sorted col order
        all_cols_sorted = sorted(candidates_by_col.keys())

        if len(tokens) != len(all_cols_sorted):
            # Column count mismatch — log and skip rather than crash
            # This can happen on continuation rows or malformed lines
            continue

        row = {"precinct": precinct}
        for col, token in zip(all_cols_sorted, tokens):
            try:
                row[col] = int(token)
            except ValueError:
                row[col] = 0

        result.precinct_rows.append(row)

    return result


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def write_summary(results: list[RaceResult], out_path: Path):
    """Write race-level candidate summary CSV."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "election_date", "race_name", "candidate_name",
            "total_votes", "pct", "is_meta"
        ])
        for r in results:
            for c in r.candidates:
                writer.writerow([
                    r.election_date,
                    r.race_name,
                    c.name,
                    c.total_votes,
                    c.pct,
                    c.is_meta
                ])
    print(f"  Summary written: {out_path} ({sum(len(r.candidates) for r in results)} rows)")


def write_precincts(results: list[RaceResult], out_path: Path):
    """Write precinct-level results in long format."""
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "election_date", "race_name", "precinct",
            "candidate_name", "votes", "is_meta"
        ])
        for r in results:
            # Build col -> candidate lookup
            col_map = {c.col_num: c for c in r.candidates}
            for row in r.precinct_rows:
                precinct = row["precinct"]
                for col, votes in row.items():
                    if col == "precinct":
                        continue
                    cand = col_map.get(col)
                    if cand is None:
                        continue
                    writer.writerow([
                        r.election_date,
                        r.race_name,
                        precinct,
                        cand.name,
                        votes,
                        cand.is_meta
                    ])
    total_rows = sum(
        len(r.precinct_rows) * len(r.candidates) for r in results
    )
    print(f"  Precincts written: {out_path} (~{total_rows} rows)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse Bexar County canvass PDF to CSV"
    )
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument(
        "--outdir",
        default=None,
        help="Output directory (default: ../../data/processed/ relative to script)"
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)

    # Determine output directory
    if args.outdir:
        out_dir = Path(args.outdir)
    else:
        # Default: two levels up from script, into data/processed
        script_dir = Path(__file__).resolve().parent
        out_dir = script_dir.parent.parent / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = pdf_path.stem.lower().replace(" ", "_")
    summary_path = out_dir / f"{stem}_summary.csv"
    precincts_path = out_dir / f"{stem}_precincts.csv"

    print(f"\nParsing: {pdf_path.name}")

    # Extract text
    print("  Extracting text from PDF...")
    text = extract_text(pdf_path)

    # Get election date from document header
    election_date = extract_election_date(text)
    print(f"  Election date detected: {election_date}")

    # Split into race sections
    sections = split_into_sections(text)
    print(f"  Found {len(sections)} sections to parse")

    # Parse each section
    results = []
    for i, section in enumerate(sections):
        r = parse_section(section, election_date)
        if r and r.candidates:
            results.append(r)
            cand_count = sum(1 for c in r.candidates if not c.is_meta)
            print(f"    [{i+1}] {r.race_name} — {cand_count} candidates, "
                  f"{len(r.precinct_rows)} precincts")

    if not results:
        print("  WARNING: No races parsed. Check PDF format.")
        sys.exit(1)

    print(f"\n  Total races parsed: {len(results)}")

    # Write outputs
    write_summary(results, summary_path)
    write_precincts(results, precincts_path)

    print(f"\nDone. Files in: {out_dir}\n")


if __name__ == "__main__":
    main()
