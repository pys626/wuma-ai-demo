"""Streamlit Cloud finals entry: independent synthetic database, saved cases, read-only UI."""
import os
from pathlib import Path
import tempfile

os.environ["WUMA_DEMO_MODE"] = "true"
os.environ["WUMA_DEMO_READ_ONLY"] = "true"
os.environ["CODE_EXECUTION_ENABLED"] = "false"
os.environ.setdefault("WUMA_AI_DB_PATH", str(Path(tempfile.gettempdir()) / "wuma_finals_20261010.db"))

import streamlit as st
import database as db
import demo_mode
import finals_demo_data
import app

try:
    demo_mode.prepare_demo_database(db.DB_PATH)
    db.init_db()
    finals_demo_data.seed_finals_cases()
except (ValueError, OSError, db.sqlite3.Error) as error:
    st.error(f"决赛Demo启动已停止：{error}")
    st.stop()

demo_mode.DEMO_STUDENT_NAME = "案例学生甲"
st.info("决赛版 · 合成作答案例与已保存的真实模型评价。当前页面只读，不重新调用AI；完整本地版支持提交、答辩与教师复核。")
app.main()
