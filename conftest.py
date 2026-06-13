"""pytest 全局配置：把项目根目录加入 sys.path，确保能 import backend.*。"""
import sys
from pathlib import Path

# 把 /root/enterprise-kb 加进 sys.path（如果还没在）
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
