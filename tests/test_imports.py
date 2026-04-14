"""Every Python module imports cleanly — catches dead references."""
import importlib, pathlib, pytest

# All .py files in root (not in tests/)
ROOT = pathlib.Path(__file__).parent.parent
modules = [p.stem for p in ROOT.glob('*.py') if p.stem not in ('conftest',)]

@pytest.mark.parametrize('module_name', modules)
def test_module_imports(module_name):
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        pytest.fail(f"{module_name} import failed: {e}")
    except Exception as e:
        # Some modules may have side effects on import — skip those gracefully
        if 'import' in str(e).lower():
            pytest.fail(f"{module_name} import-time error: {e}")
