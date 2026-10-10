"""Chinese prompts for native uploaders, scoped to each individual control."""
import json
import streamlit as st


def chinese_file_uploader(label, *, file_type, key, limit_text, help=None):
    # These selectors match Streamlit 1.52; keep native browsing and drag/drop.
    scope = key + '_upload'
    instruction = json.dumps('将' + file_type.upper() + '文件拖到这里', ensure_ascii=False)
    limits = json.dumps(limit_text, ensure_ascii=False)
    st.html(f"""<style>
    .st-key-{scope} [data-testid="stFileUploaderDropzoneInstructions"] > div > span {{
        font-size: 0 !important;
    }}
    .st-key-{scope} [data-testid="stFileUploaderDropzoneInstructions"] > div > span:first-child::after {{
        content: {instruction};
        font-size: 1rem;
    }}
    .st-key-{scope} [data-testid="stFileUploaderDropzoneInstructions"] > div > span:last-child {{
        white-space: normal;
    }}
    .st-key-{scope} [data-testid="stFileUploaderDropzoneInstructions"] > div > span:last-child::after {{
        content: {limits};
        font-size: 0.875rem;
    }}
    .st-key-{scope} [data-testid="stFileUploaderDropzone"] button {{
        font-size: 0 !important;
    }}
    .st-key-{scope} [data-testid="stFileUploaderDropzone"] button > * {{
        display: none;
    }}
    .st-key-{scope} [data-testid="stFileUploaderDropzone"] button::after {{
        content: "选择文件";
        font-size: 1rem;
    }}
    </style>""")
    with st.container(key=scope):
        return st.file_uploader(label, type=[file_type], key=key, help=help)
