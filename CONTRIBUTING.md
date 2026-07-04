# Contributing

Keep changes small and covered by `python -m unittest -q`.

Before opening a PR, run:

```bash
python -m unittest -q
python -m py_compile gh_suggest.py
python gh_suggest.py --version
```
