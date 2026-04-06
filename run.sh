#!/bin/bash
cd "$(dirname "$0")"

# Install dependencies if needed
pip3 install -q streamlit pandas plotly openpyxl python-dotenv requests 2>/dev/null

# Run the dashboard
python3 -m streamlit run app.py
