import os
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        ui = Path(self.root) / "frontend/dist"
        if (ui / "index.html").is_file():
            build_data.setdefault("force_include", {})[str(ui)] = "iic_copilot/_ui"
        elif os.environ.get("IIC_BUILD_REQUIRE_UI") == "1":
            raise RuntimeError("Build IIC exige npm run build em frontend/")
