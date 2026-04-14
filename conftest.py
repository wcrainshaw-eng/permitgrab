"""Shared pytest config."""
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
os.environ.setdefault('TESTING', '1')
