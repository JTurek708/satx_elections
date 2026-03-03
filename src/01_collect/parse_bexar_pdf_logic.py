"""
parse_bexar_pdf_logic.py
------------------------
Core parsing logic for Bexar County canvass PDFs.
Imported by both 01_parse_bexar_pdf.py and the test suite.
"""

import re
import csv
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    col_num: int
    name: str
    total_votes: int = 0
    pct: float = 0.0
    is_meta: bool = False  # True for OVER VOTES / UNDER VOTES


@dataclass
class RaceResult:
    race_name: str
    election_date: str
    candidates: list = field(default_factory=list)
    precinct_rows: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

DIVIDER_RE = re.compile(r"={40,}")

LEGEND_RE = re.compile(
    r"^\s*(\d{2})\s*=\s*(.+?)\s{2,}([\d,]+)\s+([\d.]+)\s*$"
)
LEGEND_NO_PCT_RE = re.compile(
    r"^\s*(\d{2})\s*=\s*(.+?)\s{2,}([\d,]+)\s*$"
)

RACE_HEADER_RE = re.compile(
    r"^((?:MAYOR|Member of Council|City of San Antonio Bond|PROPOSITION).+?)(?:\s{3,}|$)",
    re.IGNORECASE
)

PRECINCT_ROW_RE = re.compile(r"^(\d{4})\s+([\d\s.]+)$")

DATE_RE = re.compile(
    r"((?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},\s+\d{4})"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_name(raw: str) -> str:
    return " ".join(raw.strip().split())


def parse_votes(s: str) -> int:
    return int(s.replace(",", "").strip())


def extract_election_date(text: str) -> str:
    m = DATE_RE.search(text)
    return m.group(1) if m else "Unknown"


def split_into_sections(text: str) -> list:
    parts = DIVIDER_RE.split(text)
    return [p.strip() for p in parts if len(p.strip()) > 100]


# ---------------------------------------------------------------------------
# Section parser
# ---------------------------------------------------------------------------

def parse_section(section: str, election_date: str):
    lines = section.splitlines()

    # Find race name
    race_name = None
    for line in lines[:10]:
        m = RACE_HEADER_RE.match(line.strip())
        if m:
            race_name = clean_name(m.group(1))
            break
    if not race_name:
        for line in lines[:10]:
            stripped = line.strip()
            if stripped and not stripped[0].isdigit() and len(stripped) > 5:
                race_name = clean_name(stripped)
                break
    if not race_name:
        return None

    result = RaceResult(race_name=race_name, election_date=election_date)

    # Parse candidate legend
    candidates_by_col = {}

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

    # Parse precinct rows
    all_cols_sorted = sorted(candidates_by_col.keys())

    for line in lines:
        m = PRECINCT_ROW_RE.match(line.strip())
        if not m:
            continue

        precinct = m.group(1)
        raw_vals = m.group(2).strip()

        # Handle the ". 81" alignment padding used in fixed-width format
        cleaned = re.sub(r"\.\s+", " ", raw_vals)
        cleaned = re.sub(r"\.\s*$", "", cleaned)
        tokens = cleaned.split()

        if len(tokens) != len(all_cols_sorted):
            continue  # column mismatch — skip malformed row

        row = {"precinct": precinct}
        for col, token in zip(all_cols_sorted, tokens):
            try:
                row[col] = int(token)
            except ValueError:
                row[col] = 0

        result.precinct_rows.append(row)

    return result


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def write_summary(results: list, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "election_date", "race_name", "candidate_name",
            "total_votes", "pct", "is_meta"
        ])
        for r in results:
            for c in r.candidates:
                writer.writerow([
                    r.election_date, r.race_name, c.name,
                    c.total_votes, c.pct, c.is_meta
                ])


def write_precincts(results: list, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "election_date", "race_name", "precinct",
            "candidate_name", "votes", "is_meta"
        ])
        for r in results:
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
                        r.election_date, r.race_name, precinct,
                        cand.name, votes, cand.is_meta
                    ])
