"""Visual theme for the Streamlit app, adapted from the TEN Capital "Deck Analyzer" design.

Navy surfaces, a faint tri-color glow echoing the logo's three figures, Sora
headings, Inter body text and JetBrains Mono labels. Streamlit's own widgets are
restyled through their stable data-testid hooks; nothing here changes behaviour.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from html import escape

import streamlit as st

from . import config

NAVY_950, NAVY_900, NAVY_800, NAVY_700 = "#0B1526", "#101E33", "#16283F", "#1E354F"
CORAL, CORAL_SOFT, AMBER, TEAL = "#EE5A4E", "#F0776C", "#F3A22A", "#35BEBB"
INK_100, INK_300, INK_500, INK_600 = "#F3F6FA", "#C4D0E0", "#7E90A8", "#5C6E86"

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap');

/* ---- canvas ---------------------------------------------------------- */
.stApp {{ background: {NAVY_950}; color: {INK_100}; }}
.stApp::before {{
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(480px 380px at 14% 8%, rgba(238,90,78,0.16), transparent 60%),
    radial-gradient(480px 380px at 86% 6%, rgba(243,162,42,0.13), transparent 60%),
    radial-gradient(560px 420px at 50% 100%, rgba(53,190,187,0.14), transparent 60%);
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{ max-width: 1120px; padding-top: 2.5rem; position: relative; z-index: 1; }}
[data-testid="stSidebar"] {{ background: {NAVY_900}; border-right: 1px solid {NAVY_700}; }}

/* ---- type ------------------------------------------------------------ */
.stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp td, .stApp th,
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stCaptionContainer"] {{
  font-family: 'Inter', sans-serif;
}}
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {{ font-family: 'Sora', sans-serif; letter-spacing: -0.01em; color: {INK_100}; }}
.stApp code {{ font-family: 'JetBrains Mono', monospace; }}
[data-testid="stCaptionContainer"] {{ color: {INK_500}; }}

/* ---- brand lockup ---------------------------------------------------- */
.ten-brand {{ display: flex; align-items: center; gap: 12px; margin: 0 0 22px 4px; }}
.ten-brand img {{ width: 34px; height: 34px; }}
.ten-brand-word {{ font-family: 'Sora', sans-serif; font-weight: 800; font-size: 15px; letter-spacing: .04em;
  line-height: 1.15; text-transform: uppercase; color: {INK_100}; }}
.ten-brand-word span {{ display: block; font-weight: 600; font-size: 10px; letter-spacing: .22em; color: {INK_500}; margin-top: 2px; }}

/* ---- hero card ------------------------------------------------------- */
.ten-card {{
  background: linear-gradient(180deg, {NAVY_900} 0%, {NAVY_800} 100%);
  border: 1px solid {NAVY_700}; border-radius: 20px; padding: 40px 44px 34px;
  box-shadow: 0 30px 60px -20px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.03);
  position: relative; overflow: hidden; margin-bottom: 28px;
}}
.ten-card::after {{ content: ""; position: absolute; top: -2px; left: 44px; right: 44px; height: 2px;
  background: linear-gradient(90deg, {CORAL}, {AMBER}, {TEAL}); border-radius: 2px; }}
.ten-eyebrow {{ display: flex; align-items: center; gap: 8px; font-family: 'JetBrains Mono', monospace;
  font-size: 11px; letter-spacing: .14em; text-transform: uppercase; color: {TEAL}; margin-bottom: 14px; }}
.ten-eyebrow::before {{ content: ""; width: 6px; height: 6px; border-radius: 50%; background: {TEAL};
  box-shadow: 0 0 0 3px rgba(53,190,187,.18); }}
.ten-card h1 {{ font-family: 'Sora', sans-serif; font-size: 30px; font-weight: 700; line-height: 1.25; margin: 0 0 12px; padding: 0; }}
.ten-card h1 .arrow {{ color: {INK_500}; font-weight: 400; margin: 0 6px; }}
.ten-card h1 .to {{ background: linear-gradient(90deg, {CORAL_SOFT}, {AMBER}); -webkit-background-clip: text;
  background-clip: text; color: transparent; }}
.ten-lede {{ color: {INK_300}; font-size: 15px; line-height: 1.6; margin: 0; max-width: 62ch; }}
.ten-steps {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 22px; }}
.ten-step {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .06em; color: {INK_500};
  border: 1px solid {NAVY_700}; border-radius: 999px; padding: 5px 11px; background: rgba(255,255,255,.015); }}
.ten-step.done {{ color: {TEAL}; border-color: rgba(53,190,187,.45); }}
.ten-step.now {{ color: {INK_100}; border-color: {CORAL}; background: rgba(238,90,78,.08); }}

/* ---- section headings ------------------------------------------------ */
.ten-section {{ margin: 34px 0 12px; }}
.ten-section .num {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .14em;
  text-transform: uppercase; color: {TEAL}; }}
.ten-section .title {{ font-family: 'Sora', sans-serif; font-weight: 700; font-size: 21px; color: {INK_100}; margin-top: 2px; }}

/* ---- dropzone -------------------------------------------------------- */
[data-testid="stFileUploaderDropzone"] {{
  border: 1.5px dashed {NAVY_700}; border-radius: 14px; background: rgba(255,255,255,.015);
  transition: border-color .18s ease, background .18s ease;
}}
[data-testid="stFileUploaderDropzone"]:hover {{ border-color: {TEAL}; background: rgba(53,190,187,.05); }}
[data-testid="stFileUploaderDropzoneInstructions"] span, [data-testid="stFileUploaderDropzoneInstructions"] small {{
  font-family: 'JetBrains Mono', monospace;
}}

/* ---- buttons --------------------------------------------------------- */
[data-testid="stBaseButton-primary"], [data-testid="stDownloadButton"] button[kind="primary"] {{
  background: linear-gradient(90deg, {CORAL} 0%, {CORAL_SOFT} 45%, {AMBER} 100%);
  color: #17130E; border: none; border-radius: 12px; font-family: 'Sora', sans-serif; font-weight: 700;
  box-shadow: 0 10px 24px -10px rgba(238,90,78,.45); transition: filter .15s ease, transform .15s ease;
}}
[data-testid="stBaseButton-primary"]:hover {{ filter: brightness(1.06); transform: translateY(-1px); color: #17130E; }}
[data-testid="stBaseButton-primary"] p {{ font-family: 'Sora', sans-serif; font-weight: 700; }}
[data-testid="stBaseButton-primary"]:disabled {{ background: {NAVY_800}; color: {INK_600}; box-shadow: none; }}
[data-testid="stBaseButton-secondary"] {{ border-radius: 10px; border-color: {NAVY_700}; background: {NAVY_900}; }}
.stButton:has([data-testid="stBaseButton-primary"]) button {{ min-height: 52px; font-size: 15px; }}

/* ---- surfaces -------------------------------------------------------- */
[data-testid="stMetric"] {{ background: {NAVY_900}; border: 1px solid {NAVY_700}; border-radius: 14px; padding: 14px 16px; }}
[data-testid="stMetricLabel"] p {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .08em;
  text-transform: uppercase; color: {INK_500}; }}
[data-testid="stMetricValue"] {{ font-family: 'Sora', sans-serif; }}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {{ border: 1px solid {NAVY_700}; border-radius: 12px; overflow: hidden; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 6px; border-bottom: 1px solid {NAVY_700}; }}
.stTabs [data-baseweb="tab"] {{ font-family: 'Sora', sans-serif; font-weight: 600; }}
[data-testid="stImage"] img {{ border-radius: 10px; box-shadow: 0 30px 60px -24px rgba(0,0,0,.7); }}

/* ---- disclosure & footer --------------------------------------------- */
.ten-disclosure {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid {NAVY_700};
  font-size: 12px; line-height: 1.6; color: {INK_500}; }}
.ten-disclosure code {{ font-family: 'JetBrains Mono', monospace; background: {NAVY_950}; border: 1px solid {NAVY_700};
  color: {INK_300}; padding: 2px 6px; border-radius: 5px; font-size: 11.5px; }}
.ten-footer {{ text-align: center; margin: 48px 0 8px; font-family: 'JetBrains Mono', monospace; font-size: 11px;
  letter-spacing: .08em; color: {INK_600}; text-transform: uppercase; }}

@media (max-width: 640px) {{
  .ten-card {{ padding: 30px 22px 26px; }}
  .ten-card h1 {{ font-size: 23px; }}
}}
"""

STEPS = ("Materials", "Evidence check", "Generate", "Review & export")


@lru_cache(maxsize=1)
def _mark_data_uri() -> str:
    path = config.ASSETS_DIR / "ten_capital_mark.png"
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def apply_theme() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def brand() -> None:
    mark = _mark_data_uri()
    img = f'<img src="{mark}" alt="TEN Capital Network logo">' if mark else ""
    st.markdown(f'<div class="ten-brand">{img}<div class="ten-brand-word">Ten Capital<span>Network</span></div></div>',
                unsafe_allow_html=True)


def hero(current_step: int) -> None:
    """The intro card. current_step is 0-based; earlier steps show as done."""
    chips = "".join(
        f'<span class="ten-step {"done" if i < current_step else "now" if i == current_step else ""}">'
        f'{i + 1:02d} · {escape(name)}</span>'
        for i, name in enumerate(STEPS))
    st.markdown(f"""
<div class="ten-card">
  <div class="ten-eyebrow">Competition Analyzer</div>
  <h1>Pitch Deck<span class="arrow">&rarr;</span><span class="to">Competition Analysis</span></h1>
  <p class="ten-lede">Upload a pitch deck and any supporting material. Claude explains how customers solve the
  problem today, why that fails, and where an investable wedge emerges, and says so plainly when the
  materials do not support one. You get a one-page memo PDF with source references.</p>
  <div class="ten-steps">{chips}</div>
</div>""", unsafe_allow_html=True)


def section(number: int, title: str) -> None:
    st.markdown(f'<div class="ten-section"><div class="num">{number:02d} · Step</div>'
                f'<div class="title">{escape(title)}</div></div>', unsafe_allow_html=True)


def disclosure() -> None:
    if config.notifications_enabled():
        to = ", ".join(f"<code>{escape(r)}</code>" for r in config.notify_recipients())
        email = f" A copy of every generated analysis is emailed to {to}."
    else:
        email = " Result emails are off."
    st.markdown(f'<div class="ten-disclosure">Uploaded files are processed on this server and their text is sent '
                f'to the Anthropic API for analysis.{email}</div>', unsafe_allow_html=True)


def footer() -> None:
    st.markdown('<div class="ten-footer">Powered by TEN Capital Network</div>', unsafe_allow_html=True)
