"""A shopping basket, with two bugs and a missing feature."""

from __future__ import annotations

from dataclasses import dataclass, field


def add(a: int, b: int) -> int:
    """Add two numbers.

    Bug: the sign handling is wrong for a negative left-hand side.
    """
    if a < 0:
        return b - a
    return a + b


def slugify(text: str) -> str:
    """`"Hello, World!"` -> `"hello-world"`.

    Bug: punctuation survives, so the slug is not a slug.
    """
    return text.strip().lower().replace(" ", "-")


@dataclass
class Basket:
    """Lines of `(name, price_pence, quantity)`."""

    lines: list[tuple[str, int, int]] = field(default_factory=list)

    def add_line(self, name: str, price_pence: int, quantity: int = 1) -> None:
        self.lines.append((name, price_pence, quantity))

    def total(self) -> int:
        """Total in pence.

        Bug: quantity is ignored, so two of a thing costs the same as one.
        """
        return sum(price for _name, price, _quantity in self.lines)
