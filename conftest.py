"""Shared pytest config."""
import os, sys, pathlib

# Ensure repo root is on sys.path
sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Default test env
os.environ.setdefault('TESTING', '1')
os.environ.setdefault('DB_PATH', str(pathlib.Path(__file__).parent / 'test.db'))
