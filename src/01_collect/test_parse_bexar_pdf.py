"""
test_parse_bexar_pdf.py
-----------------------
Validates the parser against known values from the May 2009 Bexar County
canvass document, using the extracted text directly (no PDF needed).

Run:
    python test_parse_bexar_pdf.py
"""

import re
import sys
from pathlib import Path

# Add script directory to path so we can import the parser
sys.path.insert(0, str(Path(__file__).parent))

from parse_bexar_pdf_logic import (
    split_into_sections,
    parse_section,
    extract_election_date,
    Candidate,
)

# ---------------------------------------------------------------------------
# The raw text extracted from the May 2009 PDF (key sections only)
# ---------------------------------------------------------------------------

SAMPLE_TEXT = """
Numbered Key Canvass                              Bexar County, Texas
RUN DATE:05/11/09 09:01 AM                        May 9, 2009

   01 = REGISTERED VOTERS - TOTAL                773,680
   02 = BALLOTS CAST - TOTAL                      89,835
   03 = VOTER TURNOUT - TOTAL                              11.61

====================================================================================================================================

                                                   VOTES  PERCENT
MAYOR CITY OF SAN ANTONIO
  VOTE FOR  1
   01 = Napoleon Madrid                              188     .25
   02 = Lauro Bustamante                             441     .58
   06 = Trish DeBerry-Mejia                       22,031   28.98
   07 = Diane Cibrian                              6,181    8.13
   08 = Julian Castro                             42,745   56.23
   09 = Sheila D. McNeil                           2,962    3.90
   10 = OVER VOTES                                     1
   11 = UNDER VOTES                                  586
                                   ------------------------------------------------------------------
                                       01    02    06    07    08    09    10    11
                                   ------------------------------------------------------------------
1001                                    0     1    13     4    62     1     0     0
1002                                    0     0     7     3    38     1     0     0
1003                                    1     1    25    12   171     9     0     2
2045                                    0     2    69    14   407     5     0     2
2046                                    1     1    76    16   447     8     0     7
3011                                    1     1   211    96   247     8     0     7

====================================================================================================================================

                                                   VOTES  PERCENT
Member of Council, Place 1 CITY OF SAN ANTONIO
  VOTE FOR  1
   01 = Chris Forbrich                             2,588   38.81
   02 = Mary Alice P. Cisneros                     3,682   55.22
   03 = Ruby Krebs                                   398    5.97
   04 = OVER VOTES                                     0
   05 = UNDER VOTES                                  212
                                   ------------------------------
                                       01    02    03    04    05
                                   ------------------------------
1001                                   37    34     6     0     4
2003                                   38   198    10     0     8
4001                                   79   109    26     0    12
4155                                  133   138    14     0    11

====================================================================================================================================

                                                   VOTES  PERCENT
Member of Council, Place 2 CITY OF SAN ANTONIO
  VOTE FOR  1
   01 = Byron Miller                               1,747   39.33
   02 = Dan Martinez                                 815   18.35
   03 = Ron Wright                                   489   11.01
   04 = Bryant N. Livingston                         157    3.53
   05 = Ivy R. Taylor                              1,234   27.78
   06 = OVER VOTES                                     1
   07 = UNDER VOTES                                  157
                                   ------------------------------------------
                                       01    02    03    04    05    06    07
                                   ------------------------------------------
4002                                   17     8     6     2    14     0     4
4036                                  120   103    19    15   110     0     7
4070                                  112    37    52    13   101     0     8

====================================================================================================================================

                                                   VOTES  PERCENT
Member of Council, Place 9 CITY OF SAN ANTONIO
  VOTE FOR  1
   01 = Elisa Chan                                 7,833   65.44
   02 = Weston Martinez                            2,739   22.88
   03 = William Davidson                           1,397   11.67
   04 = OVER VOTES                                     0
   05 = UNDER VOTES                                  558
                                   ------------------------------
                                       01    02    03    04    05
                                   ------------------------------
3001                                  155    28    28     0     9
3042                                  232    64    42     0    12
3079                                  343    94    63     0    17
3102                                  332    83    40     0    13

====================================================================================================================================
"""

# ---------------------------------------------------------------------------
# Known ground-truth values for assertions
# ---------------------------------------------------------------------------

KNOWN = {
    "mayor": {
        "race_pattern": "MAYOR",
        "candidates": {
            "Julian Castro": {"total_votes": 42745, "pct": 56.23},
            "Trish DeBerry-Mejia": {"total_votes": 22031, "pct": 28.98},
            "Diane Cibrian": {"total_votes": 6181, "pct": 8.13},
        },
        "precincts": {
            # precinct: {candidate_name: votes}
            "1001": {"Julian Castro": 62, "Trish DeBerry-Mejia": 13},
            "2045": {"Julian Castro": 407, "Trish DeBerry-Mejia": 69},
            "3011": {"Julian Castro": 247, "Trish DeBerry-Mejia": 211},
        }
    },
    "place1": {
        "race_pattern": "Place 1",
        "candidates": {
            "Mary Alice P. Cisneros": {"total_votes": 3682, "pct": 55.22},
            "Chris Forbrich": {"total_votes": 2588, "pct": 38.81},
        },
        "precincts": {
            "1001": {"Chris Forbrich": 37, "Mary Alice P. Cisneros": 34},
            "4001": {"Chris Forbrich": 79, "Mary Alice P. Cisneros": 109},
        }
    },
    "place2": {
        "race_pattern": "Place 2",
        "candidates": {
            "Byron Miller": {"total_votes": 1747, "pct": 39.33},
            "Ivy R. Taylor": {"total_votes": 1234, "pct": 27.78},
        },
        "precincts": {
            "4036": {"Byron Miller": 120, "Ivy R. Taylor": 110},
        }
    },
    "place9": {
        "race_pattern": "Place 9",
        "candidates": {
            "Elisa Chan": {"total_votes": 7833, "pct": 65.44},
            "Weston Martinez": {"total_votes": 2739, "pct": 22.88},
        },
        "precincts": {
            "3079": {"Elisa Chan": 343, "Weston Martinez": 94},
            "3102": {"Elisa Chan": 332, "Weston Martinez": 83},
        }
    },
}


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_tests():
    passed = 0
    failed = 0

    def ok(label):
        nonlocal passed
        print(f"  ✓  {label}")
        passed += 1

    def fail(label, detail=""):
        nonlocal failed
        marker = f"  ✗  {label}"
        if detail:
            marker += f"\n       {detail}"
        print(marker)
        failed += 1

    print("\n── Election date detection ─────────────────────────────────────")
    date = extract_election_date(SAMPLE_TEXT)
    if date == "May 9, 2009":
        ok(f"Date detected: '{date}'")
    else:
        fail("Date detection", f"got '{date}', expected 'May 9, 2009'")

    print("\n── Section splitting ───────────────────────────────────────────")
    sections = split_into_sections(SAMPLE_TEXT)
    # Expect 4 race sections (Mayor + Places 1, 2, 9)
    # The first fragment before the first divider is the doc header — filtered
    race_sections = [s for s in sections if any(
        kw in s for kw in ["MAYOR", "Member of Council"]
    )]
    if len(race_sections) == 4:
        ok(f"Found 4 race sections")
    else:
        fail("Section count", f"found {len(race_sections)}, expected 4")

    print("\n── Race parsing ────────────────────────────────────────────────")
    parsed_races = {}
    for s in race_sections:
        r = parse_section(s, "May 9, 2009")
        if r:
            parsed_races[r.race_name] = r

    for key, spec in KNOWN.items():
        pattern = spec["race_pattern"]
        race = next(
            (r for name, r in parsed_races.items() if pattern in name),
            None
        )
        if race is None:
            fail(f"Race '{pattern}' not found in parsed output")
            continue
        ok(f"Race found: '{race.race_name}'")

        # Check candidate totals
        cand_by_name = {c.name: c for c in race.candidates if not c.is_meta}
        for cname, expected in spec["candidates"].items():
            c = cand_by_name.get(cname)
            if c is None:
                fail(f"  Candidate '{cname}' not found in {pattern}")
                continue
            if c.total_votes == expected["total_votes"]:
                ok(f"  {cname}: total_votes={c.total_votes}")
            else:
                fail(
                    f"  {cname} total_votes",
                    f"got {c.total_votes}, expected {expected['total_votes']}"
                )
            if abs(c.pct - expected["pct"]) < 0.01:
                ok(f"  {cname}: pct={c.pct}")
            else:
                fail(
                    f"  {cname} pct",
                    f"got {c.pct}, expected {expected['pct']}"
                )

        # Check precinct rows
        precinct_map = {row["precinct"]: row for row in race.precinct_rows}
        col_map = {c.col_num: c for c in race.candidates}

        for precinct, expected_votes in spec["precincts"].items():
            row = precinct_map.get(precinct)
            if row is None:
                fail(f"  Precinct {precinct} not found in {pattern}")
                continue
            for cname, exp_votes in expected_votes.items():
                # Find column number for this candidate
                cand = next(
                    (c for c in race.candidates if c.name == cname), None
                )
                if cand is None:
                    fail(f"  Can't find col for {cname}")
                    continue
                actual = row.get(cand.col_num, "MISSING")
                if actual == exp_votes:
                    ok(f"  Precinct {precinct} | {cname}: {actual} votes")
                else:
                    fail(
                        f"  Precinct {precinct} | {cname}",
                        f"got {actual}, expected {exp_votes}"
                    )

    print("\n── Meta-row handling ───────────────────────────────────────────")
    mayor_race = next(
        (r for name, r in parsed_races.items() if "MAYOR" in name), None
    )
    if mayor_race:
        meta = [c for c in mayor_race.candidates if c.is_meta]
        real = [c for c in mayor_race.candidates if not c.is_meta]
        if len(meta) == 2:
            ok(f"Mayor: 2 meta rows (OVER/UNDER VOTES)")
        else:
            fail("Meta row count", f"got {len(meta)}, expected 2")
        if len(real) == 6:
            ok(f"Mayor: 6 real candidates")
        else:
            fail("Real candidate count", f"got {len(real)}, expected 6")

    print(f"\n{'─'*60}")
    total = passed + failed
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  |  {failed} FAILED ← investigate before running on full PDF")
    else:
        print("  ✓ All clear — parser ready for full PDF")
    print()

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
