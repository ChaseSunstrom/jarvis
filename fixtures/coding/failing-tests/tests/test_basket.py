"""The specification. Jarvis Code must make these pass without editing them.

`unittest`, not pytest, and deliberately: the check runs inside a container
with **no network** (`network: none`), so anything that would have to be
installed first could never go green. The standard library is what is
definitely there.

Run as `python -m unittest discover -s . -p "test_*.py"` from the repository
root, which is what `checks:` declares.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.basket import Basket, add, slugify  # noqa: E402


class TestAdd(unittest.TestCase):
    def test_it_handles_negatives(self):
        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add(-2, 3), 1)
        self.assertEqual(add(-5, -5), -10)


class TestSlugify(unittest.TestCase):
    def test_it_drops_punctuation(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("  Trailing space  "), "trailing-space")
        self.assertEqual(slugify("Two   spaces"), "two-spaces")


class TestBasket(unittest.TestCase):
    def test_it_counts_quantities(self):
        basket = Basket()
        basket.add_line("apple", 30, 3)
        basket.add_line("bread", 120)
        self.assertEqual(basket.total(), 210)


if __name__ == "__main__":  # pragma: no cover - the check runs discovery
    unittest.main()
