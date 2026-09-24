"""Plain-language read-back of a feature control frame (UI redesign,
Lot I).

The frame builder shows, under its preview, what the frame says in
words -- "Position within a Ø0.1 mm cylindrical zone at MMC, relative
to datums A, B, C." -- so a misplaced prefix or a forgotten datum is
caught before the frame lands on the drawing. It also carries the few
ISO 1101 rules the builder enforces or warns about:

- form tolerances (straightness, flatness, circularity, cylindricity)
  never reference a datum: the builder skips the datum step for them;
- orientation, runout, concentricity and symmetry always need one: the
  read-back says so while none is given.
"""

from __future__ import annotations

from annoter.model.gdt import (
    CHARACTERISTIC_META,
    Characteristic,
    DatumRef,
    Family,
    GdtRow,
)

_DATUM_REQUIRED_EXTRA = (Characteristic.CONCENTRICITY, Characteristic.SYMMETRY)

_ZONE_WORDS: dict[str, str] = {
    "": "within a {v} mm wide zone",
    "Ø": "within a Ø{v} mm cylindrical zone",
    "SØ": "within an SØ{v} mm spherical zone",
    "R": "within an R{v} mm zone",
    "SR": "within an SR{v} mm spherical zone",
}

_TOL_MODIFIER_WORDS: dict[str, str] = {
    "M": " at maximum material (MMC)",
    "L": " at least material (LMC)",
    "P": ", projected tolerance zone",
    "E": ", envelope requirement",
}

_DATUM_MODIFIER_WORDS: dict[str, str] = {
    "M": " at MMC",
    "L": " at LMC",
    "P": " projected",
    "F": " in free state",
}


def family_of(c: Characteristic) -> Family:
    return CHARACTERISTIC_META[c][0]


def name_of(c: Characteristic) -> str:
    return CHARACTERISTIC_META[c][1]


def takes_datums(c: Characteristic) -> bool:
    """False for form tolerances, which never reference a datum."""
    return family_of(c) is not Family.FORM


def needs_datum(c: Characteristic) -> bool:
    """True where ISO 1101 requires at least one datum reference."""
    return (
        family_of(c) in (Family.ORIENTATION, Family.RUNOUT)
        or c in _DATUM_REQUIRED_EXTRA
    )


def _datum_words(ref: DatumRef) -> str:
    text = "-".join(s.strip().upper() for s in ref.letters if s.strip())
    if ref.modifier:
        text += _DATUM_MODIFIER_WORDS.get(ref.modifier, "")
    return text


def row_datums(row: GdtRow) -> list[DatumRef]:
    refs = [row.datum_primary, row.datum_secondary, row.datum_tertiary]
    return [r for r in refs if not r.is_empty()]


def describe_row(row: GdtRow) -> str:
    """One sentence saying what the row controls."""
    name = name_of(row.characteristic)
    value = row.tolerance_value.strip()
    if not value:
        return f"{name}: type the tolerance value."
    zone = _ZONE_WORDS.get(row.tolerance_prefix, _ZONE_WORDS[""])
    text = f"{name} {zone.format(v=value)}"
    if row.tolerance_modifier:
        text += _TOL_MODIFIER_WORDS.get(row.tolerance_modifier, "")
    datums = row_datums(row) if takes_datums(row.characteristic) else []
    if datums:
        words = [_datum_words(d) for d in datums]
        noun = "datum" if len(words) == 1 else "datums"
        text += f", relative to {noun} {', '.join(words)}"
    return text + "."


def row_warning(row: GdtRow) -> str | None:
    """A rule the row breaks, in words, or None."""
    c = row.characteristic
    if needs_datum(c) and not row_datums(row):
        return f"{name_of(c)} needs at least one datum reference."
    return None
