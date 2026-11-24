# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Shared lazy backend module handles for server operation handlers."""

from pdfmanager.server.common import lazy_module

accessibility = lazy_module("pdfmanager.accessibility")
archive = lazy_module("pdfmanager.archive")
certcrypt = lazy_module("pdfmanager.certcrypt")
certsign = lazy_module("pdfmanager.certsign")
comments = lazy_module("pdfmanager.comments")
compare = lazy_module("pdfmanager.compare")
core = lazy_module("pdfmanager.core")
filetools = lazy_module("pdfmanager.filetools")
formdetect = lazy_module("pdfmanager.formdetect")
forms = lazy_module("pdfmanager.forms")
layers = lazy_module("pdfmanager.layers")
ocr = lazy_module("pdfmanager.ocr")
office = lazy_module("pdfmanager.office")
organize = lazy_module("pdfmanager.organize")
outline = lazy_module("pdfmanager.outline")
pdfacheck = lazy_module("pdfmanager.pdfacheck")
protection = lazy_module("pdfmanager.protection")
recipes = lazy_module("pdfmanager.recipes")
redact = lazy_module("pdfmanager.redact")
risks = lazy_module("pdfmanager.risks")
scan = lazy_module("pdfmanager.scan")
search = lazy_module("pdfmanager.search")
watchfolders = lazy_module("pdfmanager.watchfolders")
