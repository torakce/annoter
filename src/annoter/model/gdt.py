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


@dataclass
class GdtState:
    """Full state of a feature control frame."""

    characteristic: Characteristic = Characteristic.PERPENDICULARITY
    diameter_prefix: bool = False
    tolerance_value: str = ""
    tolerance_modifier: str | None = None
    datum_primary: DatumRef = field(default_factory=DatumRef)
    datum_secondary: DatumRef = field(default_factory=DatumRef)
    datum_tertiary: DatumRef = field(default_factory=DatumRef)

    # ------------------------------------------------------------------
    # display
    # ------------------------------------------------------------------
    def tolerance_display(self) -> str:
        parts: list[str] = []
        if self.diameter_prefix:
            parts.append("Ø")  # Ø
        if self.tolerance_value.strip():
            parts.append(self.tolerance_value.strip())
        text = " ".join(parts)
        if self.tolerance_modifier:
            text += enclosed(self.tolerance_modifier)
        return text

    def datum_displays(self) -> list[str]:
        """Returns the trailing non-empty datum cells (kept in order)."""
        cells = [
            self.datum_primary.display(),
            self.datum_secondary.display(),
            self.datum_tertiary.display(),
        ]
        # Drop trailing empty cells; keep gaps in the middle (rare but
        # legal -- e.g. primary + tertiary -- by promoting the tertiary).
        while cells and not cells[-1]:
            cells.pop()
        return cells

    # ------------------------------------------------------------------
    # serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "characteristic": self.characteristic.value,
            "diameter_prefix": self.diameter_prefix,
            "tolerance_value": self.tolerance_value,
            "tolerance_modifier": self.tolerance_modifier,
            "datum_primary": self.datum_primary.to_dict(),
            "datum_secondary": self.datum_secondary.to_dict(),
            "datum_tertiary": self.datum_tertiary.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> GdtState:
        return cls(
            characteristic=Characteristic(
                data.get("characteristic", Characteristic.PERPENDICULARITY.value)
            ),
            diameter_prefix=bool(data.get("diameter_prefix", False)),
            tolerance_value=str(data.get("tolerance_value", "")),
            tolerance_modifier=data.get("tolerance_modifier"),
            datum_primary=DatumRef.from_dict(data.get("datum_primary", {})),
            datum_secondary=DatumRef.from_dict(data.get("datum_secondary", {})),
            datum_tertiary=DatumRef.from_dict(data.get("datum_tertiary", {})),
        )
