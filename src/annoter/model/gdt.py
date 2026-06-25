"""GD&T (ISO 1101) model: characteristic, datum reference, full FCF state.

The state is plain data and serializable to a dict so M4 can persist it
inside the PDF (`fitz.Annot.set_info(content=json.dumps(state.to_dict()))`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Family(Enum):
    FORM = "Form"
    PROFILE = "Profile"
    ORIENTATION = "Orientation"
    LOCATION = "Location"
    RUNOUT = "Runout"


class Characteristic(Enum):
    """The 14 ISO 1101 geometric characteristics."""

    STRAIGHTNESS = "straightness"
    FLATNESS = "flatness"
    CIRCULARITY = "circularity"
    CYLINDRICITY = "cylindricity"
    PROFILE_LINE = "profile_line"
    PROFILE_SURFACE = "profile_surface"
    PARALLELISM = "parallelism"
    PERPENDICULARITY = "perpendicularity"
    ANGULARITY = "angularity"
    POSITION = "position"
    CONCENTRICITY = "concentricity"
    SYMMETRY = "symmetry"
    CIRCULAR_RUNOUT = "circular_runout"
    TOTAL_RUNOUT = "total_runout"


# UI metadata: family + display name. Order within family matters for
# the palette layout.
CHARACTERISTIC_META: dict[Characteristic, tuple[Family, str]] = {
    Characteristic.STRAIGHTNESS: (Family.FORM, "Straightness"),
    Characteristic.FLATNESS: (Family.FORM, "Flatness"),
    Characteristic.CIRCULARITY: (Family.FORM, "Circularity"),
    Characteristic.CYLINDRICITY: (Family.FORM, "Cylindricity"),
    Characteristic.PROFILE_LINE: (Family.PROFILE, "Profile of a line"),
    Characteristic.PROFILE_SURFACE: (Family.PROFILE, "Profile of a surface"),
    Characteristic.PARALLELISM: (Family.ORIENTATION, "Parallelism"),
    Characteristic.PERPENDICULARITY: (Family.ORIENTATION, "Perpendicularity"),
    Characteristic.ANGULARITY: (Family.ORIENTATION, "Angularity"),
    Characteristic.POSITION: (Family.LOCATION, "Position"),
    Characteristic.CONCENTRICITY: (Family.LOCATION, "Concentricity"),
    Characteristic.SYMMETRY: (Family.LOCATION, "Symmetry"),
    Characteristic.CIRCULAR_RUNOUT: (Family.RUNOUT, "Circular runout"),
    Characteristic.TOTAL_RUNOUT: (Family.RUNOUT, "Total runout"),
}


def by_family() -> dict[Family, list[Characteristic]]:
    out: dict[Family, list[Characteristic]] = {f: [] for f in Family}
    for c, (fam, _) in CHARACTERISTIC_META.items():
        out[fam].append(c)
    return out


# Tolerance modifiers (material/state). Stored as the bare letter; the
# enclosed-character glyphs are produced for display only.
ALLOWED_TOLERANCE_MODIFIERS: tuple[str, ...] = ("M", "L", "P", "E")
ALLOWED_DATUM_MODIFIERS: tuple[str, ...] = ("M", "L", "P", "F")

# Tolerance zone prefixes, stored as the literal display string.
TOLERANCE_PREFIXES: tuple[str, ...] = ("Ø", "R", "SØ", "SR")

TOLERANCE_PREFIX_NAMES: dict[str, str] = {
    "Ø": "Diameter (cylindrical zone)",
    "R": "Radius",
    "SØ": "Spherical diameter",
    "SR": "Spherical radius",
}

# Full ISO 1101 names, used for tooltips in the editor UI.
MODIFIER_NAMES: dict[str, str] = {
    "M": "Maximum material requirement",
    "L": "Least material requirement",
    "P": "Projected tolerance zone",
    "E": "Envelope requirement",
    "F": "Free state",
}


_ENCLOSED = {
    "M": "Ⓜ",  # Ⓜ
    "L": "Ⓛ",  # Ⓛ
    "P": "Ⓟ",  # Ⓟ
    "E": "Ⓔ",  # Ⓔ
    "F": "Ⓕ",  # Ⓕ
}


def enclosed(letter: str) -> str:
    """Return the enclosed-character form for a modifier letter."""
    return _ENCLOSED.get(letter, letter)


@dataclass
class DatumRef:
    """A single FCF datum cell.

    `letters` holds one or more uppercase letters; multiple letters are
    rendered joined with '-' (composite datum, e.g. 'A-B').
    """

    letters: list[str] = field(default_factory=list)
    modifier: str | None = None  # one of ALLOWED_DATUM_MODIFIERS, or None

    def is_empty(self) -> bool:
        return not any(s.strip() for s in self.letters)

    def display(self) -> str:
        if self.is_empty():
            return ""
        text = "-".join(s.strip().upper() for s in self.letters if s.strip())
        if self.modifier:
            text += enclosed(self.modifier)
        return text

    def to_dict(self) -> dict:
        return {"letters": list(self.letters), "modifier": self.modifier}

    @classmethod
    def from_dict(cls, data: dict) -> DatumRef:
        return cls(
            letters=list(data.get("letters", [])),
            modifier=data.get("modifier"),
        )


def _tolerance_display(prefix: str, value: str, modifier: str | None) -> str:
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    if value.strip():
        parts.append(value.strip())
    text = " ".join(parts)
    if modifier:
        text += enclosed(modifier)
    return text


def _datum_displays(
    d1: DatumRef, d2: DatumRef, d3: DatumRef
) -> list[str]:
    cells = [d1.display(), d2.display(), d3.display()]
    # Drop trailing empty cells; keep gaps in the middle (rare but legal).
    while cells and not cells[-1]:
        cells.pop()
    return cells


@dataclass
class GdtRow:
    """One tolerance line of a (possibly composite) feature control frame.

    Each row carries its own characteristic symbol, tolerance value and
    datum references. The item merges the symbol cell only across
    consecutive rows that share the same `characteristic`.
    """

    characteristic: Characteristic = Characteristic.PERPENDICULARITY
    tolerance_prefix: str = ""
    tolerance_value: str = ""
    tolerance_modifier: str | None = None
    datum_primary: DatumRef = field(default_factory=DatumRef)
    datum_secondary: DatumRef = field(default_factory=DatumRef)
    datum_tertiary: DatumRef = field(default_factory=DatumRef)

    def tolerance_display(self) -> str:
        return _tolerance_display(
            self.tolerance_prefix,
            self.tolerance_value,
            self.tolerance_modifier,
        )

    def datum_displays(self) -> list[str]:
        return _datum_displays(
            self.datum_primary, self.datum_secondary, self.datum_tertiary
        )

    def is_empty(self) -> bool:
        return (
            not self.tolerance_prefix
            and not self.tolerance_value.strip()
            and not self.tolerance_modifier
            and self.datum_primary.is_empty()
            and self.datum_secondary.is_empty()
            and self.datum_tertiary.is_empty()
        )

    def to_dict(self) -> dict:
        return {
            "characteristic": self.characteristic.value,
            "tolerance_prefix": self.tolerance_prefix,
            "tolerance_value": self.tolerance_value,
            "tolerance_modifier": self.tolerance_modifier,
            "datum_primary": self.datum_primary.to_dict(),
            "datum_secondary": self.datum_secondary.to_dict(),
            "datum_tertiary": self.datum_tertiary.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> GdtRow:
        prefix = str(data.get("tolerance_prefix", ""))
        if not prefix and data.get("diameter_prefix"):
            prefix = "Ø"
        return cls(
            characteristic=Characteristic(
                data.get(
                    "characteristic", Characteristic.PERPENDICULARITY.value
                )
            ),
            tolerance_prefix=prefix,
            tolerance_value=str(data.get("tolerance_value", "")),
            tolerance_modifier=data.get("tolerance_modifier"),
            datum_primary=DatumRef.from_dict(data.get("datum_primary", {})),
            datum_secondary=DatumRef.from_dict(data.get("datum_secondary", {})),
            datum_tertiary=DatumRef.from_dict(data.get("datum_tertiary", {})),
        )


@dataclass
class GdtState:
    """Full state of a feature control frame.

    The first tolerance row is held in the flat `tolerance_*` / `datum_*`
    fields (backward compatible with single-row FCFs persisted before the
    composite rework); extra composite rows live in `additional_rows`.
    `upper_text` / `lower_text` are the texts shown above / below the
    frame, and an optional auxiliary frame (`aux_symbol` + `aux_text`) is
    appended to the right.
    """

    characteristic: Characteristic = Characteristic.PERPENDICULARITY
    # One of TOLERANCE_PREFIXES, or "" for none.
    tolerance_prefix: str = ""
    tolerance_value: str = ""
    tolerance_modifier: str | None = None
    datum_primary: DatumRef = field(default_factory=DatumRef)
    datum_secondary: DatumRef = field(default_factory=DatumRef)
    datum_tertiary: DatumRef = field(default_factory=DatumRef)
    # Composite rows beyond the first.
    additional_rows: list[GdtRow] = field(default_factory=list)
    upper_text: str = ""
    lower_text: str = ""
    # Optional auxiliary frame appended to the right (e.g. // | A-B).
    aux_symbol: Characteristic | None = None
    aux_text: str = ""

    # ------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------
    def row0(self) -> GdtRow:
        """The first row, synthesized from the flat fields."""
        return GdtRow(
            characteristic=self.characteristic,
            tolerance_prefix=self.tolerance_prefix,
            tolerance_value=self.tolerance_value,
            tolerance_modifier=self.tolerance_modifier,
            datum_primary=self.datum_primary,
            datum_secondary=self.datum_secondary,
            datum_tertiary=self.datum_tertiary,
        )

    def all_rows(self) -> list[GdtRow]:
        """First row plus every composite row, in display order."""
        return [self.row0(), *self.additional_rows]

    # ------------------------------------------------------------------
    # display (row 0 -- kept for backward compatibility)
    # ------------------------------------------------------------------
    def tolerance_display(self) -> str:
        return self.row0().tolerance_display()

    def datum_displays(self) -> list[str]:
        return self.row0().datum_displays()

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        data = {
            "characteristic": self.characteristic.value,
            "tolerance_prefix": self.tolerance_prefix,
            "tolerance_value": self.tolerance_value,
            "tolerance_modifier": self.tolerance_modifier,
            "datum_primary": self.datum_primary.to_dict(),
            "datum_secondary": self.datum_secondary.to_dict(),
            "datum_tertiary": self.datum_tertiary.to_dict(),
        }
        if self.additional_rows:
            data["additional_rows"] = [
                r.to_dict() for r in self.additional_rows
            ]
        if self.upper_text:
            data["upper_text"] = self.upper_text
        if self.lower_text:
            data["lower_text"] = self.lower_text
        if self.aux_symbol is not None:
            data["aux_symbol"] = self.aux_symbol.value
        if self.aux_text:
            data["aux_text"] = self.aux_text
        return data

    @classmethod
    def from_dict(cls, data: dict) -> GdtState:
        # PDFs annotated before the prefix rework stored a boolean
        # "diameter_prefix" -- map it to the literal prefix.
        prefix = str(data.get("tolerance_prefix", ""))
        if not prefix and data.get("diameter_prefix"):
            prefix = "Ø"
        aux_raw = data.get("aux_symbol")
        return cls(
            characteristic=Characteristic(
                data.get("characteristic", Characteristic.PERPENDICULARITY.value)
            ),
            tolerance_prefix=prefix,
            tolerance_value=str(data.get("tolerance_value", "")),
            tolerance_modifier=data.get("tolerance_modifier"),
            datum_primary=DatumRef.from_dict(data.get("datum_primary", {})),
            datum_secondary=DatumRef.from_dict(data.get("datum_secondary", {})),
            datum_tertiary=DatumRef.from_dict(data.get("datum_tertiary", {})),
            additional_rows=[
                GdtRow.from_dict(r)
                for r in data.get("additional_rows", [])
            ],
            upper_text=str(data.get("upper_text", "")),
            lower_text=str(data.get("lower_text", "")),
            aux_symbol=Characteristic(aux_raw) if aux_raw else None,
            aux_text=str(data.get("aux_text", "")),
        )
