"""Tolerance model for inline tolerance runs inside a text annotation.

A tolerance is either symmetric (a single "+/-value") or bilateral (an
upper and a lower offset, stacked on two lines). This is the going-
forward model: a tolerance is a run *inside* a text annotation, not an
annotation type of its own, so the nominal value is simply the text
around it.

It replaced the standalone Dimension annotation (retired in Lot L,
2026-09): a PDF that still holds one reopens as a text carrying the same
value and tolerance run (`services.pdf_export.legacy_dimension_runs`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ToleranceMode(str, Enum):
    SYMMETRIC = "symmetric"  # single line: +/-value
    BILATERAL = "bilateral"  # two stacked lines: +upper / -lower


MODE_NAMES: dict[ToleranceMode, str] = {
    ToleranceMode.SYMMETRIC: "Symmetric (±)",
    ToleranceMode.BILATERAL: "Bilateral (+/-)",
}

PLUS_MINUS = "±"


def _signed(value: str, sign: str) -> str:
    """Prefix `value` with `sign` unless the user typed one already.

    Offsets are stored as bare magnitudes by convention, but an upper
    offset may legitimately be negative (a shaft entirely undersized),
    so an explicit sign in the input is kept as-is.
    """
    v = value.strip()
    if not v:
        return ""
    return v if v[0] in "+-" else f"{sign}{v}"


@dataclass(frozen=True)
class Tolerance:
    mode: ToleranceMode = ToleranceMode.SYMMETRIC
    value: str = ""  # SYMMETRIC
    upper: str = ""  # BILATERAL
    lower: str = ""  # BILATERAL

    def is_empty(self) -> bool:
        if self.mode is ToleranceMode.SYMMETRIC:
            return not self.value.strip()
        return not (self.upper.strip() or self.lower.strip())

    def lines(self) -> list[str]:
        """The rendered text lines, top to bottom."""
        if self.mode is ToleranceMode.SYMMETRIC:
            v = self.value.strip()
            return [f"{PLUS_MINUS}{v}"] if v else []
        return [
            s
            for s in (_signed(self.upper, "+"), _signed(self.lower, "-"))
            if s
        ]

    def plain(self) -> str:
        """Single-line plain-text form, for PDF /Contents and item labels."""
        return "/".join(self.lines())

    def to_dict(self) -> dict:
        data: dict = {"mode": self.mode.value}
        if self.mode is ToleranceMode.SYMMETRIC:
            if self.value:
                data["value"] = self.value
        else:
            if self.upper:
                data["upper"] = self.upper
            if self.lower:
                data["lower"] = self.lower
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Tolerance":
        return cls(
            mode=ToleranceMode(data.get("mode", ToleranceMode.SYMMETRIC.value)),
            value=str(data.get("value", "")),
            upper=str(data.get("upper", "")),
            lower=str(data.get("lower", "")),
        )
