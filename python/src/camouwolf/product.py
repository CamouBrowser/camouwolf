from pathlib import Path

from camoucore import CHROMIUM, Product

PRODUCT = Product('camouwolf', 'Camouwolf', CHROMIUM, package=Path(__file__).parent)
