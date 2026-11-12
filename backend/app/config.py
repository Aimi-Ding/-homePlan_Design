"""全局配置。所有路径都是绝对路径，避免 uvicorn 从不同 cwd 启动时行为不一致。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取：只在环境变量没设置时生效，不覆盖已有值。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")

VENDOR = BASE_DIR / "vendor" / "house-plan-designer"
SCRIPTS = VENDOR / "scripts"
TEMPLATE = VENDOR / "assets" / "template.html"
VAR = BASE_DIR / "var"
PROJECTS_DIR = VAR / "projects"
WEB_DIR = BASE_DIR / "web"

VAR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{VAR / 'app.db'}")

# 单机起量的并发。生产换成 RQ/Celery 时把 MAX_JOBS 交给队列侧控制。
MAX_JOBS = int(os.getenv("MAX_JOBS", "2"))

# rule: 只跑几何脚本，产出墙体+房间（无需模型）
# llm : 调用视觉模型补全
# auto: 有 key 用 llm，没有则退化为 rule
PIPELINE_MODE = os.getenv("PIPELINE_MODE", "auto").lower()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
LLM_MAX_ROUNDS = int(os.getenv("LLM_MAX_ROUNDS", "3"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))

# 登录：console = 验证码打到服务端日志（本地开发）；aliyun = 阿里云短信
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "console").lower()
SMS_DEBUG = os.getenv("SMS_DEBUG", "1" if SMS_PROVIDER == "console" else "0") == "1"
CODE_TTL_SECONDS = int(os.getenv("CODE_TTL_SECONDS", "300"))
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"

# 下载/保存前是否要求留手机号（软门槛，只做前端拦截 + 记录，不挡直链）
LEAD_REQUIRED = os.getenv("LEAD_REQUIRED", "1") == "1"

# 阿里云短信（SMS_PROVIDER=aliyun 时必填）
ALIYUN_ACCESS_KEY_ID = os.getenv("ALIYUN_ACCESS_KEY_ID", "")
ALIYUN_ACCESS_KEY_SECRET = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "")
ALIYUN_SMS_SIGN_NAME = os.getenv("ALIYUN_SMS_SIGN_NAME", "")
ALIYUN_SMS_TEMPLATE_CODE = os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "")
ALIYUN_SMS_REGION = os.getenv("ALIYUN_SMS_REGION", "cn-hangzhou")
ALIYUN_SMS_PARAM_KEY = os.getenv("ALIYUN_SMS_PARAM_KEY", "code")

# 上游模板是压缩产物，升级时必须显式确认，避免静默变更渲染结果。
PINNED_TEMPLATE_SHA256 = os.getenv("TEMPLATE_SHA256", "")


def llm_enabled() -> bool:
    if PIPELINE_MODE == "rule":
        return False
    if PIPELINE_MODE == "llm":
        return bool(LLM_API_KEY)
    return bool(LLM_API_KEY)


def effective_mode() -> str:
    return "llm" if llm_enabled() else "rule"
