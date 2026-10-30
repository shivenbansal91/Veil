# Copied into .venv\Lib\site-packages so every venv python run (activated or not, pip
# included) keeps temp files and caches on D:. C: is full. See CLAUDE.md §12.1.
import os

for name, sub in {"TMP": "tmp", "TEMP": "tmp", "PIP_CACHE_DIR": "pip"}.items():
    os.environ[name] = os.path.join(r"D:\Veil-cache", sub)
    os.makedirs(os.environ[name], exist_ok=True)
