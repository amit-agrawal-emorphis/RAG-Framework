"""Doc-management Streamlit shell; extend with ingestion controls as needed."""
import streamlit as st

st.set_page_config(page_title="Doc management", layout="wide")
st.title("Document management")
st.caption(
    "Placeholder UI. Run ingestion from the CLI with "
    "`python doc-management/backend/launcher.py` (or `python -m ingest_and_export`)."
)
