"""Latest v1.4 interactive demo, with private synthetic visitor workspaces."""
import os

os.environ["WUMA_DEMO_MODE"] = "false"
os.environ["WUMA_INTERACTIVE_DEMO"] = "true"
os.environ["CODE_EXECUTION_ENABLED"] = "false"

import interactive_demo
interactive_demo.main()
