"""CIF-based formation resolver: derive train_class and coach count from CIF data.

Used as a fallback when Darwin enrichment is disabled or fails. Data comes
entirely from the CIF BS record (already parsed), so no external API is needed.

Train class comes from the seating_class field (BS col 60):
  B = Business + First + Standard  → "1st & Standard"
  F = First + Standard             → "1st & Standard"
  S = Standard only                → "Standard"
  blank                            → "" (unknown)

Coach count comes from the timing_load field (BS cols 48-51), which is a
~3-4 char code identifying the specific rolling-stock class (e.g. "321",
"700 ", "387 "). A lookup table covers the most common UK EMU/DMU classes.
Returns None for unknown types so the column stays blank rather than wrong.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Seating class mapping
# ---------------------------------------------------------------------------

_SEATING_CLASS_MAP: dict[str, str] = {
    "B": "1st & Standard",  # Business + First + Standard
    "F": "1st & Standard",  # First + Standard
    "S": "Standard",        # Standard only
}


def cif_train_class(seating_class: str) -> str:
    """Map a CIF seating_class code to a human-readable train class string."""
    return _SEATING_CLASS_MAP.get(seating_class.strip().upper(), "")


# ---------------------------------------------------------------------------
# Timing load → coach count lookup
# ---------------------------------------------------------------------------
# Key = timing_load stripped (first 3-4 chars).  Value = typical coach count.
# These are the most common UK EMU/DMU classes; unknown codes return None.

_TIMING_LOAD_COACHES: dict[str, int] = {
    # Class 3xx — Southern/Thameslink area EMUs
    "313": 3, "314": 3, "315": 4, "317": 4, "318": 3,
    "319": 4, "320": 3, "321": 4, "322": 4, "323": 3,
    "325": 4, "331": 4, "332": 4, "333": 4,
    "345": 9,   # Elizabeth line (Aventra 9-car)
    "350": 4, "357": 4, "360": 5,
    "374": 16,  # Eurostar e320
    "375": 4, "376": 4, "377": 4, "378": 5,
    "379": 4, "380": 4, "382": 5, "385": 4,
    "387": 4, "390": 9,   # Pendolino 9-car
    "395": 6,  # Javelin
    "397": 5,
    # Class 4xx — South Western / Southeastern
    "442": 5, "444": 10, "450": 4, "455": 4, "456": 2,
    "458": 4, "460": 8, "465": 4, "466": 2,
    # Class 4xx — Island line
    "483": 2, "484": 2,
    # Class 5xx — Transpennine/Caledonian
    "507": 3, "508": 3,
    # Class 7xx — New generation
    "700": 8, "701": 10, "707": 5, "710": 5,
    "717": 8, "745": 12,
    # Class 8xx — IEP / AT300
    "800": 9, "801": 5, "802": 5, "803": 5, "805": 5,
    "807": 7, "810": 5,
    # DMUs
    "158": 2, "168": 4, "170": 3, "171": 4, "172": 2,
    "175": 3, "180": 5, "185": 3, "195": 2,
    "196": 2, "197": 3,
    # HST/IC125
    "HST": 8,  # 2 power cars + 7-9 MkIII coaches; 8 is typical
    # Loco-hauled intercity
    "91 ": 9,  # Class 91 + 8 coaches + DVT
    "92 ": 13,
}


def cif_coach_count(timing_load: str, power_type: str = "") -> Optional[int]:
    """Estimate number of coaches from CIF timing_load and power_type codes.

    Returns None when the unit type is not in the lookup table.
    """
    tl = timing_load.strip()
    if not tl:
        return None

    # Try full key first (e.g. "345"), then first 3 chars
    for key in (tl, tl[:3]):
        if key in _TIMING_LOAD_COACHES:
            return _TIMING_LOAD_COACHES[key]

    # HST special-case via power_type
    if power_type.strip().upper() in ("HST",):
        return _TIMING_LOAD_COACHES.get("HST")

    return None
