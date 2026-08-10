"""conftest.py - Pytest Configuration and Global Fixtures.

Configures environment variables natively on test suite startup before any local
modules are loaded, guaranteeing proper test-sandboxing.
"""

import os

# Enforce test database sandbox path on boot
os.environ["DB_PATH"] = "test_omatasku.db"
