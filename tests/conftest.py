import sys
from pathlib import Path

# 将 backend 目录加入 sys.path, 使 app 包可直接导入
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
