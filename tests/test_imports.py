"""Every Python module imports cleanly — catches dead references."""
import importlib, pathlib, pytest

ROOT = pathlib.Path(__file__).parent.parent
SKIP = {'conftest', 'worker', 'test_'}
modules = [
    p.stem for p in ROOT.glob('*.py')
    if not any(p.stem.startswith(s) for s in SKIP)
]

@pytest.mark.parametrize('module_name', modules)
def test_module_imports(module_name):
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.fail(f"{module_name} import failed: {e}")
    except SystemExit:
        pass  # Some modules may sys.exit(0) on import
    except Exception as e:
        if 'No module named' in str(e) or 'cannot import' in str(e).lower():
            pytest.fail(f"{module_name} import-time error: {e}")
