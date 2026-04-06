"""Simple password authentication for the dashboard."""

import streamlit as st
import hmac


def check_password():
    """Returns True if the user has entered the correct password."""
    if st.session_state.get("authenticated"):
        return True

    try:
        correct_password = st.secrets["APP_PASSWORD"]
    except (FileNotFoundError, KeyError):
        import os
        correct_password = os.getenv("APP_PASSWORD", "")

    if not correct_password:
        return True

    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        html, body, [class*="css"] { font-family: 'Inter', system-ui, sans-serif; }
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown("")
        st.markdown("")
        st.markdown(
            '<h1 style="color:#1E40AF;text-align:center;font-size:1.8rem;margin-bottom:0;">PPL Dashboard</h1>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p style="text-align:center;color:#475569;margin-top:4px;margin-bottom:2rem;">Enter your team password to continue</p>',
            unsafe_allow_html=True,
        )
        password = st.text_input("Password", type="password", key="login_password", label_visibility="collapsed", placeholder="Enter password...")
        if st.button("Sign In", type="primary", use_container_width=True):
            if hmac.compare_digest(password, correct_password):
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")

    return False
