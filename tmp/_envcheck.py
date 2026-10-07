import importlib.util as u
import os

for name in ("playwright", "httpx", "fastapi", "uvicorn", "pydantic", "starlette"):
    print(name, bool(u.find_spec(name)))

base = os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "ms-playwright")
print("ms-playwright dir:", os.path.isdir(base))
if os.path.isdir(base):
    print("  entries:", os.listdir(base))
