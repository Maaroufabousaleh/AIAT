#!/usr/bin/env python
"""Run the complete MAS test suite from a single file.

Usage
-----
    python run_tests.py              # all tests, quiet
    python run_tests.py -v           # verbose
    python run_tests.py -k phase7    # keyword filter
    python run_tests.py --co         # collect-only (list tests)

Any extra arguments are forwarded directly to pytest.
"""

import sys
import pytest

sys.exit(pytest.main(sys.argv[1:]))
