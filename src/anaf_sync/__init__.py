"""anaf-sync — scheduled local archiver for RO e-Factura invoices.

Lists every e-Factura message in the retention window, downloads the ones not
seen before, and files each artifact under a path rendered from a user-defined
template of invoice context variables. Built on :mod:`anafpy`; reuses the
credentials and token store written by ``anafpy auth login``.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
