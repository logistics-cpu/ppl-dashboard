"""Shared theme constants and CSS for PPL Dashboard v2."""

# Design system colors
PRIMARY = "#1E40AF"
PRIMARY_LIGHT = "#3B82F6"
SECONDARY = "#EFF6FF"
ACCENT = "#F59E0B"
BG = "#F8FAFC"
TEXT = "#0F172A"
TEXT_MUTED = "#475569"
SUCCESS = "#059669"
WARNING = "#D97706"
DANGER = "#DC2626"
BORDER = "#E2E8F0"

# Status colors for stock life
CRITICAL_BG = "#FEE2E2"
CRITICAL_TEXT = "#991B1B"
WARNING_BG = "#FEF3C7"
WARNING_TEXT = "#92400E"
OK_BG = "#D1FAE5"
OK_TEXT = "#065F46"

# Plotly chart template
CHART_COLORS = ["#1E40AF", "#EF4444", "#10B981", "#F59E0B", "#8B5CF6", "#EC4899", "#06B6D4"]

PLOTLY_LAYOUT = dict(
    font=dict(family="Inter, system-ui, sans-serif", color=TEXT),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=40, r=20, t=50, b=40),
    hoverlabel=dict(bgcolor="white", font_size=13, bordercolor=BORDER),
    colorway=CHART_COLORS,
    xaxis=dict(gridcolor="#F1F5F9", zerolinecolor="#E2E8F0"),
    yaxis=dict(gridcolor="#F1F5F9", zerolinecolor="#E2E8F0"),
)


def inject_css():
    """Inject global CSS into the Streamlit page."""
    import streamlit as st
    st.markdown(f"""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        /* Global font */
        html, body, [class*="css"] {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
        }}

        /* Page title */
        h1 {{
            color: {PRIMARY} !important;
            font-weight: 700 !important;
            letter-spacing: -0.025em !important;
            padding-bottom: 0.25rem !important;
        }}

        /* Section headers */
        h2, h3 {{
            color: {TEXT} !important;
            font-weight: 600 !important;
        }}

        /* Metric cards */
        [data-testid="stMetric"] {{
            background: white;
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        }}
        [data-testid="stMetricLabel"] {{
            color: {TEXT_MUTED} !important;
            font-size: 0.8rem !important;
            font-weight: 500 !important;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        [data-testid="stMetricValue"] {{
            color: {TEXT} !important;
            font-weight: 700 !important;
            font-size: 1.6rem !important;
        }}

        /* Dataframes */
        [data-testid="stDataFrame"] {{
            border: 1px solid {BORDER};
            border-radius: 12px;
            overflow: hidden;
        }}

        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 0;
            border-bottom: 2px solid {BORDER};
        }}
        .stTabs [data-baseweb="tab"] {{
            padding: 10px 20px;
            font-weight: 500;
            color: {TEXT_MUTED};
        }}
        .stTabs [aria-selected="true"] {{
            color: {PRIMARY} !important;
            border-bottom-color: {PRIMARY} !important;
        }}

        /* Buttons */
        .stButton > button[kind="primary"] {{
            background: {PRIMARY} !important;
            border: none !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            padding: 0.5rem 1.5rem !important;
            transition: background 200ms ease;
        }}
        .stButton > button[kind="primary"]:hover {{
            background: {PRIMARY_LIGHT} !important;
        }}

        /* Sidebar */
        [data-testid="stSidebar"] {{
            background: white !important;
            border-right: 1px solid {BORDER} !important;
        }}
        [data-testid="stSidebar"] h1 {{
            font-size: 1.3rem !important;
        }}

        /* Info/Success/Warning/Error boxes */
        .stAlert {{
            border-radius: 10px !important;
        }}

        /* File uploader */
        [data-testid="stFileUploader"] {{
            border-radius: 10px;
        }}

        /* Selectbox and inputs */
        [data-baseweb="select"], [data-baseweb="input"] {{
            border-radius: 8px !important;
        }}

        /* Hide Streamlit branding but keep sidebar toggle */
        #MainMenu {{visibility: hidden;}}
        footer {{visibility: hidden;}}

        /* Ensure sidebar expand button is always visible */
        [data-testid="collapsedControl"] {{
            display: flex !important;
            visibility: visible !important;
            z-index: 999;
        }}
    </style>
    """, unsafe_allow_html=True)


def metric_row(metrics, cols=None):
    """Render a row of metric cards. metrics: list of (label, value, delta?)"""
    import streamlit as st
    if cols is None:
        cols = st.columns(len(metrics))
    for col, m in zip(cols, metrics):
        if len(m) == 3:
            col.metric(m[0], m[1], m[2])
        else:
            col.metric(m[0], m[1])


def status_badge(text, status="ok"):
    """Return HTML for a colored status badge."""
    colors = {
        "critical": (CRITICAL_BG, CRITICAL_TEXT),
        "warning": (WARNING_BG, WARNING_TEXT),
        "ok": (OK_BG, OK_TEXT),
    }
    bg, fg = colors.get(status, colors["ok"])
    return f'<span style="background:{bg};color:{fg};padding:3px 10px;border-radius:20px;font-size:0.8rem;font-weight:600;">{text}</span>'


def page_header(title, subtitle=None):
    """Render a consistent page header."""
    import streamlit as st
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    st.markdown("")  # spacing
