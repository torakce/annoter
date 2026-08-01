"""Data model for the Dimension annotation (nominal value + tolerance).

A dimension is a nominal numeric value with an optional prefix symbol
(diameter, radius...) and an optional tolerance, which is either a
single symmetric +/- value or two independently signed values stacked
above/below the nominal value (bilateral / offset tolerancing, e.g.
"+0.10 / -0.05"). Plain data, serializable to a dict so the item can be
persisted inside the PDF the same way `model.gdt.GdtState` is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DimensionPrefix(str, Enum):
    NONE = ""
    DIAMETER = "Ø"  # Ø
    RADIUS = "R"
    SPHERICAL_DIAMETER = "SØ"  # SØ
    SPHERICAL_RADIUS = "SR"
    SQUARE = "□"  # □


PREFIX_NAMES: dict[DimensionPrefix, str] = {
    DimensionPrefix.NONE: "No prefix",
    DimensionPrefix.DIAMETER: "Diameter",
    DimensionPrefix.RADIUS: "Radius",
    DimensionPrefix.SPHERICAL_DIAMETER: "Spherical diameter",
    DimensionPrefix.SPHERICAL_RADIUS: "Spherical radius",
    DimensionPrefix.SQUARE: "Square",
}


class ToleranceMode(str, Enum):
    NONE = "none"  # bare nominal value, no tolerance block
    SYMMETRIC = "symmetric"  # nominal +/- value, single line
    BILATERAL = "bilateral"  # nominal, stacked +upper / -lower lines


@dataclass
class DimensionState:
    prefix: DimensionPrefix = DimensionPrefix.NONE
    nominal: str = ""
    tolerance_mode: ToleranceMode = ToleranceMode.NONE
    tol_value: str = ""  # SYMMETRIC: the single +/- magnitude
    tol_upper: str = ""  # BILATERAL: shown as "+<tol_upper>"
    tol_lower: str = ""  # BILATERAL: shown as "-<tol_lower>"

    def is_empty(self) -> bool:
        return not self.nominal.strip()

    def prefix_display(self) -> str:
        return self.prefix.value

    @staticmethod
    def _signed(value: str, sign: str) -> str:
        v = value.strip()
        if not v:
            return ""
        return v if v[0] in "+-" else f"{sign}{v}"

    def upper_display(self) -> str:
        """The offset shown on the top line in BILATERAL mode.

        The stored value is a bare magnitude by convention, but a user
        may type an explicit sign (e.g. "-0.02" for an upper offset
        that actually goes negative), which is kept as-is.
        """
        return self._signed(self.tol_upper, "+")

    def lower_display(self) -> str:
        return self._signed(self.tol_lower, "-")

    def tolerance_lines(self) -> list[str]:
        """Text lines stacked to the right of the nominal value."""
        if self.tolerance_mode is ToleranceMode.SYMMETRIC:
            v = self.tol_value.strip()
            return [f"±{v}"] if v else []
        if self.tolerance_mode is ToleranceMode.BILATERAL:
            return [s for s in (self.upper_display(), self.lower_display()) if s]
        return []

    def display(self) -> str:
        """Single-line plain-text rendering (annotation list, tooltips)."""
        parts = [self.prefix_display() + self.nominal]
        lines = self.tolerance_lines()
        if self.tolerance_mode is ToleranceMode.SYMMETRIC and lines:
            parts.append(lines[0])
        elif self.tolerance_mode is ToleranceMode.BILATERAL and lines:
            parts.append("/".join(lines))
        return " ".join(parts)

    def to_dict(self) -> dict:
        data: dict = {"nominal": self.nominal}
        if self.prefix is not DimensionPrefix.NONE:
            data["prefix"] = self.prefix.value
        if self.tolerance_mode is not ToleranceMode.NONE:
            data["tolerance_mode"] = self.tolerance_mode.value
            if self.tol_value:
                data["tol_value"] = self.tol_value
            if self.tol_upper:
                data["tol_upper"] = self.tol_upper
            if self.tol_lower:
                data["tol_lower"] = self.tol_lower
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "DimensionState":
        return cls(
            prefix=DimensionPrefix(data.get("prefix", DimensionPrefix.NONE.value)),
            nominal=str(data.get("nominal", "")),
            tolerance_mode=ToleranceMode(
                data.get("tolerance_mode", ToleranceMode.NONE.value)
            ),
            tol_value=str(data.get("tol_value", "")),
            tol_upper=str(data.get("tol_upper", "")),
            tol_lower=str(data.get("tol_lower", "")),
        )
