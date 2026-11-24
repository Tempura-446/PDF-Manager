# Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
"""Command-line interface: ``pdfm <command> ...`` (or ``python -m pdfmanager``)."""

from pdfmanager.cli.common import *  # noqa: F401, F403
from pdfmanager.cli.common import (  # noqa: F401
    _NO_SIGNED_WARNING, _Passwords, _ask_new_password, _check_outputs,
    _default_output, _encrypt_limits, _expand, _human_size, _is_batch,
    _is_folder, _output_for, _print_plan, _reencrypt,
)
from pdfmanager.cli.commands_edit import *  # noqa: F401, F403
from pdfmanager.cli.commands_convert import *  # noqa: F401, F403
from pdfmanager.cli.commands_multi import *  # noqa: F401, F403
from pdfmanager.cli.parser import _leading_dash_ranges, _positive, build_parser, main  # noqa: F401
