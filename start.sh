#!/bin/bash
cd /Users/yitzuchang/Desktop/ppl-dashboard
exec /usr/bin/python3 -m streamlit run app.py --server.headless true --server.port 8501
