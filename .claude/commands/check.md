Run syntax checks across all Python source files and report any errors.

```bash
source venv/bin/activate

echo "=== Syntax check ==="
find . -name "*.py" \
  -not -path "./venv/*" \
  -not -path "./_archive/*" \
  -not -path "./__pycache__/*" \
  | sort \
  | xargs -I{} sh -c 'python3 -m py_compile "{}" && echo "OK: {}" || echo "FAIL: {}"'

echo ""
echo "=== Import check (main modules) ==="
python3 -c "import config; print('config OK')"
python3 -c "import extensions; print('extensions OK')"
python3 -c "import create_app; print('create_app OK')"
```

Report all files that failed and describe any errors found.
