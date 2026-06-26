"""Large stylesheet for the Equipment Intelligence Streamlit UI (kept separate from ``streamlit_app.py`` for readability)."""

APP_CSS = """
<style>
    [data-testid="stDecoration"] {
        display: none !important;
        height: 0 !important;
        min-height: 0 !important;
    }
    .stApp { margin-top: 0 !important; padding-top: 0 !important; }
    [data-testid="stAppViewContainer"] { padding-top: 0 !important; margin-top: 0 !important; }
    [data-testid="stAppViewContainer"] > .main,
    [data-testid="stAppViewContainer"] > section.main,
    section[data-testid="stMain"] { padding-top: 0 !important; margin-top: 0 !important; }
    section[data-testid="stMain"] > div, .main > div { padding-top: 0 !important; margin-top: 0 !important; }
    .main [data-testid="block-container"],
    section[data-testid="stMain"] [data-testid="block-container"] { margin-top: 0 !important; }

    .main .block-container {
        padding-top: var(--yukt-header-height) !important;
        padding-bottom: 3rem;
        max-width: 880px;
        padding-left: 4.75rem;
        overflow-x: visible !important;
    }
    .main .block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child,
    .main .block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child > div {
        margin-top: 0 !important; padding-top: 0 !important;
    }
    .main .block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:nth-child(2),
    .main .block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:nth-child(3) {
        margin-top: 0 !important; padding-top: 0 !important;
    }
    .main .block-container > div[data-testid="stVerticalBlock"] { gap: 0 !important; }
    .main { overflow-x: visible !important; padding-top: 0 !important; }

    :root {
        --yukt-left-rail-width: 98px;
        --yukt-sidebar-width-collapsed: 100px;
        --yukt-sidebar-width-expanded: 300px;
        --yukt-sidebar-width: var(--yukt-sidebar-width-collapsed);
        --yukt-chrome-border: rgba(15, 23, 42, 0.14);
        --yukt-header-rule: rgba(15, 23, 42, 0.10);
        --yukt-chrome-surface: var(--background-color);
        --yukt-header-bg: #f8fafc;
        --yukt-top-row-height: 46px;
        --yukt-header-height: 64px;
        --yukt-eq-top: 25px;
        --yukt-eq-block-h: 28px;
        --yukt-collapsed-nav-gap: 2px;
        --yukt-collapsed-nav-item-h: 44px;
        --yukt-collapsed-nav-stack-gap: 0px;
    }
    @media (prefers-color-scheme: dark) {
        :root {
            --yukt-chrome-border: rgba(255, 255, 255, 0.16);
            --yukt-header-rule: rgba(255, 255, 255, 0.12);
            --yukt-header-bg: #111827;
        }
    }

    .yukt-left-rail-gutter {
        position: fixed;
        top: var(--yukt-header-height);
        left: 0;
        width: var(--yukt-left-rail-width);
        height: calc(100vh - var(--yukt-header-height));
        background: var(--yukt-chrome-surface);
        border-right: 1px solid var(--yukt-chrome-border);
        z-index: 1005; pointer-events: none;
    }
    .yukt-left-rail {
        position: fixed;
        top: calc(var(--yukt-header-height) + 8px);
        left: 12px;
        z-index: 999999;
        display: flex; flex-direction: column; gap: 10px;
    }
    .yukt-left-rail .yukt-rail-btn {
        width: 38px; height: 38px; border-radius: 999px;
        border: 1px solid var(--yukt-chrome-border);
        background: var(--yukt-chrome-surface); color: var(--text-color);
        display: grid; place-items: center; cursor: pointer; box-shadow: none;
        font-size: 18px; line-height: 1; user-select: none; padding: 0;
        text-decoration: none !important; pointer-events: auto !important;
    }
    .yukt-left-rail .yukt-rail-btn:hover {
        background: color-mix(in srgb, var(--text-color) 6%, var(--yukt-chrome-surface));
    }
    .yukt-left-rail .yukt-rail-btn:active { transform: translateY(0.5px); }

    section[data-testid="stSidebar"].yukt-force-open {
        transform: translateX(0) !important; visibility: visible !important;
    }
    section[data-testid="stSidebar"].yukt-force-closed {
        transform: translateX(-100%) !important;
    }
    html.yukt-sidebar-open .yukt-left-rail,
    html.yukt-sidebar-open .yukt-left-rail-gutter {
        display: none !important; opacity: 0 !important; pointer-events: none !important;
    }

    h2.app-title {
        font-family: ui-serif, Georgia, "Times New Roman", Times, serif;
        font-weight: 700;
        font-size: clamp(1.15rem, 2.8vw, 1.06rem);
        letter-spacing: -0.03em; line-height: 1.15;
        background: linear-gradient(90deg, #0d9488 0%, #4c1d95 55%, #7c3aed 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        background-clip: text; margin: 0; text-align: center;
    }
    .yukt-header-bar {
        background: var(--yukt-header-bg) !important;
        opacity: 1 !important;
        border-bottom: none !important;
        margin: 0 !important;
        padding: 0.1rem 1.25rem 0.45rem calc(var(--yukt-sidebar-width) + 0.9rem);
        box-sizing: border-box;
        width: 100vw;
        left: 0;
        right: 0;
        overflow: visible;
        min-height: var(--yukt-header-height);
        max-height: var(--yukt-header-height);
        position: fixed;
        top: 0;
        z-index: 1000003;
    }
    .yukt-header-leftcap {
        position: fixed;
        top: 0;
        left: 0;
        width: var(--yukt-sidebar-width);
        height: var(--yukt-header-height);
        background: var(--yukt-header-bg) !important;
        z-index: 1000001;
        pointer-events: none;
        box-sizing: border-box;
    }
    .yukt-header-fullrule {
        position: fixed;
        top: var(--yukt-header-height);
        left: 0;
        width: 100vw;
        height: 1px;
        background: var(--yukt-header-rule) !important;
        z-index: 1000002;
        pointer-events: none;
        box-shadow: none !important;
    }
    html.yukt-sidebar-nav-expanded {
        --yukt-sidebar-width: var(--yukt-sidebar-width-expanded);
    }
    html.yukt-sidebar-nav-collapsed {
        --yukt-sidebar-width: var(--yukt-sidebar-width-collapsed);
    }
    .yukt-header-inner {
        position: relative; display: flex; align-items: center;
        min-height: calc(var(--yukt-header-height) - 0.55rem);
        max-height: calc(var(--yukt-header-height) - 0.55rem);
    }
    .yukt-header-logo-cell { position: relative; z-index: 2; flex-shrink: 0; }
    .yukt-header-logo-cell .yukt-header-logo {
        display: block;
        max-height: calc(var(--yukt-header-height) - 12px);
        max-width: min(150px, 32vw);
        width: auto; height: auto; object-fit: contain;
    }
    .yukt-header-title-cell {
        position: absolute; left: 0; right: 0; top: 0; bottom: 0;
        display: flex; align-items: center; justify-content: center;
        pointer-events: none;
    }
    .yukt-header-title-cell .app-title { pointer-events: auto; }
    .yukt-header-inner--title-only { justify-content: center; }
    .yukt-header-inner--title-only .yukt-header-title-cell {
        position: static; width: 100%; min-height: 2.35rem; justify-content: center;
    }
    @media (prefers-color-scheme: light) {
        .yukt-bubble-assistant {
            background: #f3f4f6;
            border-color: rgba(148, 163, 184, 0.4);
        }
    }

    div[data-testid="stSidebarContent"] {
        background: var(--yukt-chrome-surface); color: var(--text-color);
        border-top: 0 !important; box-shadow: none !important; padding-top: 0 !important;
    }
    section[data-testid="stSidebar"] > div[data-testid="stSidebarContent"] > div {
        padding-top: 0 !important; margin-top: 0 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child {
        margin-top: 0 !important;
    }
    section[data-testid="stSidebar"] {
        top: var(--yukt-header-height) !important;
        height: calc(100vh - var(--yukt-header-height)) !important;
        max-height: calc(100vh - var(--yukt-header-height)) !important;
        z-index: 999 !important;
        border-top: 0 !important;
        border-left: 0 !important;
        border-right: 1px solid var(--yukt-chrome-border) !important;
        border-bottom: 0 !important;
        box-shadow: none !important;
        background: var(--yukt-chrome-surface) !important;
        outline: none !important; box-sizing: border-box !important;
    }
    section[data-testid="stSidebar"] > div[data-testid="stSidebarContent"] {
        height: 100% !important;
        max-height: 100% !important;
        overflow-y: auto !important;
    }
    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] > div > div,
    div[data-testid="stSidebar"] {
        outline: none !important; box-shadow: none !important; background: transparent !important;
    }
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] .stMarkdown,
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4 {
        color: var(--text-color) !important; opacity: 1 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stCaption"],
    section[data-testid="stSidebar"] div[data-testid="stCaption"],
    section[data-testid="stSidebar"] p[data-testid="stCaption"] {
        color: var(--text-color) !important; opacity: 1 !important;
        -webkit-text-fill-color: var(--text-color) !important;
    }
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] label {
        color: var(--text-color) !important; opacity: 1 !important; font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] .stMarkdown strong { color: var(--text-color) !important; opacity: 1 !important; }
    section[data-testid="stSidebar"] .stMarkdown h1,
    section[data-testid="stSidebar"] .stMarkdown h2,
    section[data-testid="stSidebar"] .stMarkdown h3,
    section[data-testid="stSidebar"] .stMarkdown h4 { font-weight: 700 !important; opacity: 1 !important; }
    section[data-testid="stSidebar"] div[role="radiogroup"] label,
    section[data-testid="stSidebar"] div[role="radiogroup"] label span {
        color: var(--text-color) !important; opacity: 1 !important;
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] span,
    section[data-testid="stSidebar"] div[data-baseweb="select"] { color: var(--text-color) !important; }
    section[data-testid="stSidebar"] input::placeholder,
    section[data-testid="stSidebar"] textarea::placeholder {
        color: var(--text-color) !important; opacity: 0.55 !important;
    }

    section[data-testid="stSidebar"] .stButton > button {
        background: transparent !important; border: 0 !important; box-shadow: none !important;
        text-align: left !important; justify-content: flex-start !important;
        padding-left: 0.6rem !important; padding-right: 0.6rem !important;
        white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
    }
    section[data-testid="stSidebar"] .stButton { margin: 0 !important; }
    section[data-testid="stSidebar"] .stButton > button:hover { border: 0 !important; box-shadow: none !important; }
    section[data-testid="stSidebar"] .stButton > button > div,
    section[data-testid="stSidebar"] .stButton > button > span,
    section[data-testid="stSidebar"] .stButton > button [data-testid="stButtonLabel"],
    section[data-testid="stSidebar"] .stButton > button [data-testid="baseButton-label"] {
        width: 100% !important; display: flex !important; justify-content: flex-start !important;
        text-align: left !important; white-space: nowrap !important;
        overflow: hidden !important; text-overflow: ellipsis !important;
    }
    section[data-testid="stSidebar"] .stButton > button img {
        width: 55px !important; height: 18px !important; object-fit: contain !important;
    }
    section[data-testid="stSidebar"] [data-testid="stCaption"],
    section[data-testid="stSidebar"] div[data-testid="stCaption"],
    section[data-testid="stSidebar"] p[data-testid="stCaption"] {
        text-align: left !important; padding-left: 0.6rem !important; padding-right: 0.6rem !important;
        margin-top: 0.15rem !important; margin-bottom: 0.35rem !important;
        white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"] {
        margin-left: 0 !important; padding-left: 0 !important;
        margin-top: 0 !important; margin-bottom: 0 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"] > div {
        margin-top: 0 !important; margin-bottom: 0 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stContainer"],
    section[data-testid="stSidebar"] div[data-testid="stContainer"] > div,
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        padding-left: 0.35rem !important; padding-right: 0.35rem !important;
        padding-top: 0 !important; margin-left: 0 !important; margin-right: 0 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
        border: none !important; border-radius: 0 !important; background: transparent !important;
        box-shadow: none !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stContainer"] div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child {
        margin-top: 0 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0 !important; }
    section[data-testid="stSidebar"] .stButton > button {
        white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
        line-height: 2.2 !important; background: transparent !important; border: 0 !important;
        border-radius: 0 !important; padding-top: 0.12rem !important; padding-bottom: 0.12rem !important;
        margin-bottom: 0 !important; min-height: 0 !important;
    }
    section[data-testid="stSidebar"] .stButton > button:has(img) {
        border-bottom: 1px solid var(--yukt-chrome-border) !important;
        min-height: 2.05rem !important; padding-top: 0.12rem !important; padding-bottom: 0.45rem !important;
        margin-bottom: 0.2rem !important; border-radius: 0 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child .stButton > button {
        margin-top: 0 !important;
    }
    section[data-testid="stSidebar"] .st-key-yukt_product_btn {
        position: relative !important;
        top: auto !important;
        left: auto !important;
        width: 100% !important;
        z-index: 1 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button {
        width: 66px !important; min-width: 38px !important; height: 50px !important;
        margin: 0 !important; padding: 0 !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_product_btn,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_product_btn {
        position: fixed !important;
        top: var(--yukt-eq-top, 25px) !important;
        left: 0 !important;
        width: var(--yukt-sidebar-width-collapsed, 100px) !important;
        height: var(--yukt-eq-block-h, 28px) !important;
        margin: 0 !important;
        padding: 0 !important;
        z-index: 1000005 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_product_btn),
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_product_btn) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton {
        width: 100% !important;
        display: flex !important;
        justify-content: center !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button {
        width: 100% !important;
        max-width: 100px !important;
        height: 100% !important;
        min-height: 0 !important;
        padding: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button img,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button img {
        width: 40px !important;
        height: 14px !important;
        object-fit: contain !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_new_btn,
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_history_btn,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn {
        position: fixed !important;
        left: 0 !important;
        width: var(--yukt-sidebar-width-collapsed, 100px) !important;
        margin: 0 !important;
        padding: 0 !important;
        z-index: 1000004 !important;
        display: flex !important;
        justify-content: center !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_new_btn,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn {
        top: calc(var(--yukt-header-height, 64px) + 2px) !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_history_btn,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn {
        top: calc(
            var(--yukt-header-height, 64px) + 2px + var(--yukt-collapsed-nav-item-h, 44px)
        ) !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton,
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton {
        width: 100% !important;
        display: flex !important;
        justify-content: center !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton > button,
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton > button {
        width: 100% !important;
        max-width: 100px !important;
        height: var(--yukt-collapsed-nav-item-h, 44px) !important;
        min-height: var(--yukt-collapsed-nav-item-h, 44px) !important;
        margin: 0 !important;
        padding: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        border: 0 !important;
        border-bottom: 1px solid var(--yukt-chrome-border) !important;
        border-radius: 0 !important;
        box-shadow: none !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button,
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button:has(img),
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button:has(img) {
        border: 0 !important;
        border-bottom: 0 !important;
        box-shadow: none !important;
        margin-bottom: 0 !important;
        padding-bottom: 0 !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_new_btn),
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_history_btn),
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_new_btn),
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_history_btn) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] hr,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] hr {
        display: none !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(hr) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(hr) {
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0 !important;
        padding-top: calc(2px + var(--yukt-collapsed-nav-item-h, 44px) * 2) !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"]
        div[data-testid="stSidebarContent"] > div[data-testid="stVerticalBlock"]
        > div[data-testid="stElementContainer"]:first-child,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stSidebarContent"] > div[data-testid="stVerticalBlock"]
        > div[data-testid="stElementContainer"]:first-child {
        margin-top: 0 !important;
        padding-top: 0 !important;
    }
    section[data-testid="stSidebar"] .st-key-yukt_product_btn .stButton > button:has(img) { border-bottom: 0 !important; }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] {
        --yukt-nav-pad-left: 10px;
        --yukt-nav-icon-size: 18px;
        --yukt-nav-gap: 0.55rem;
        --yukt-nav-rail-width: var(--yukt-sidebar-width-collapsed, 100px);
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn {
        width: var(--yukt-sidebar-width-expanded, 300px) !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton > button,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton > button {
        width: 100% !important;
        max-width: none !important;
        height: var(--yukt-collapsed-nav-item-h, 44px) !important;
        min-height: var(--yukt-collapsed-nav-item-h, 44px) !important;
        margin: 0 !important;
        padding: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        position: relative !important;
        border: 0 !important;
        border-bottom: 1px solid var(--yukt-chrome-border) !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        background: transparent !important;
        box-sizing: border-box !important;
        font-size: 14px !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton > button > div,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton > button [data-testid="baseButton-label"],
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton > button > div,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton > button [data-testid="baseButton-label"] {
        width: 100% !important;
        min-height: var(--yukt-collapsed-nav-item-h, 44px) !important;
        display: flex !important;
        align-items: center !important;
        justify-content: flex-start !important;
        padding-left: var(--yukt-nav-rail-width, 100px) !important;
        box-sizing: border-box !important;
        gap: var(--yukt-nav-gap) !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton > button img,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton > button img {
        position: absolute !important;
        left: calc(var(--yukt-nav-rail-width, 100px) / 2) !important;
        top: 50% !important;
        transform: translate(-50%, -50%) !important;
        width: var(--yukt-nav-icon-size, 18px) !important;
        height: var(--yukt-nav-icon-size, 18px) !important;
        min-width: var(--yukt-nav-icon-size, 18px) !important;
        margin: 0 !important;
        object-fit: contain !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_new_btn .stButton > button p,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_btn .stButton > button p {
        margin: 0 !important;
    }

    section[data-testid="stSidebar"] .yukt-sidebar-navitem,
    section[data-testid="stSidebar"] .yukt-sidebar-section {
        border: 0 !important; border-radius: 0 !important; background: transparent !important;
    }
    section[data-testid="stSidebar"] .yukt-sidebar-section { margin-bottom: 0.4rem !important; }
    section[data-testid="stSidebar"] .yukt-saved-chats-label {
        display: block; margin-top: 0.55rem !important; margin-bottom: 0 !important;
        padding-bottom: 0.25rem !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stElementContainer"]:has(.yukt-saved-chats-label) { margin-bottom: 0 !important; }
    section[data-testid="stSidebar"] .yukt-saved-chats-list { padding-top: 0.4rem !important; }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {
        overflow-y: hidden !important;
        overflow-x: hidden !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(input[placeholder="Search saved chats…"]) {
        position: sticky !important;
        top: calc(2px + var(--yukt-collapsed-nav-item-h, 44px) * 2) !important;
        z-index: 1000006 !important;
        background: var(--yukt-chrome-surface) !important;
        padding-top: 0.25rem !important;
        padding-bottom: 0.25rem !important;
        border-bottom: 1px solid var(--yukt-chrome-border) !important;
        margin-bottom: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.yukt-saved-chats-label) {
        position: sticky !important;
        top: calc(46px + 2px + var(--yukt-collapsed-nav-item-h, 44px) * 2) !important;
        z-index: 1000005 !important;
        background: var(--yukt-chrome-surface) !important;
        padding-top: 0.2rem !important;
        padding-bottom: 0 !important;
        margin-bottom: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .yukt-saved-chats-gap {
        display: block !important;
        height: 15px !important;
        min-height: 15px !important;
        max-height: 15px !important;
        margin: 0 !important;
        padding: 0 !important;
        border: 0 !important;
        background: transparent !important;
        pointer-events: none !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.yukt-saved-chats-gap) {
        height: auto !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stElementContainer"]:has(.st-key-yukt_history_scroll_box) {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        padding-top: 0 !important;
        padding-bottom: 0 !important;
        min-height: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box {
        border-top: 1px solid var(--yukt-chrome-border) !important;
        margin-top: 0 !important;
        padding-top: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .yukt-history-scroll-target,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box[data-testid="stVerticalBlock"],
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] div[data-testid="stElementContainer"].st-key-yukt_history_scroll_box,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box > div[data-testid="stVerticalBlock"] {
        margin-top: 0 !important;
        padding-top: 0 !important;
        min-height: 120px !important;
        overflow-y: scroll !important;
        overflow-x: hidden !important;
        overscroll-behavior: contain !important;
        scrollbar-gutter: stable !important;
        scrollbar-width: thin !important;
        scrollbar-color: rgba(100, 116, 139, 0.55) rgba(148, 163, 184, 0.15) !important;
        padding-right: 2px !important;
        gap: 0 !important;
        display: block !important;
        align-content: flex-start !important;
        justify-content: flex-start !important;
        box-sizing: border-box !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        .st-key-yukt_history_scroll_box > div[data-testid="stVerticalBlock"]
        > div[data-testid="stElementContainer"],
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        .yukt-history-scroll-target > div[data-testid="stElementContainer"] {
        flex: 0 0 auto !important;
        min-height: 0 !important;
        height: auto !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box .stButton {
        margin: 0 !important;
        padding: 0 !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box .stButton > button {
        min-height: 0 !important;
        height: auto !important;
        line-height: 1.35 !important;
        padding: 0.1rem 0.6rem !important;
        margin: 0 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        font-size: 14px !important;
        text-align: left !important;
        justify-content: flex-start !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box .stButton > button > div,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box .stButton > button [data-testid="baseButton-label"] {
        min-height: 0 !important;
        height: auto !important;
        line-height: 1.35 !important;
        padding: 0 !important;
        margin: 0 !important;
        justify-content: flex-start !important;
        text-align: left !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .yukt-history-scroll-target::-webkit-scrollbar,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box[data-testid="stVerticalBlock"]::-webkit-scrollbar,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] div[data-testid="stElementContainer"].st-key-yukt_history_scroll_box::-webkit-scrollbar,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box > div[data-testid="stVerticalBlock"]::-webkit-scrollbar {
        width: 10px;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .yukt-history-scroll-target::-webkit-scrollbar-thumb,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box[data-testid="stVerticalBlock"]::-webkit-scrollbar-thumb,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] div[data-testid="stElementContainer"].st-key-yukt_history_scroll_box::-webkit-scrollbar-thumb,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box > div[data-testid="stVerticalBlock"]::-webkit-scrollbar-thumb {
        background: rgba(100, 116, 139, 0.55);
        border-radius: 8px;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .yukt-history-scroll-target::-webkit-scrollbar-track,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box[data-testid="stVerticalBlock"]::-webkit-scrollbar-track,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] div[data-testid="stElementContainer"].st-key-yukt_history_scroll_box::-webkit-scrollbar-track,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"] .st-key-yukt_history_scroll_box > div[data-testid="stVerticalBlock"]::-webkit-scrollbar-track {
        background: rgba(148, 163, 184, 0.15);
    }
    section[data-testid="stSidebar"] div[data-testid="stContainer"] .stButton > button:not(:has(img)),
    section[data-testid="stSidebar"] div[data-testid="stContainer"] .stButton > button:not(:has(img)) > div,
    section[data-testid="stSidebar"] div[data-testid="stContainer"] .stButton > button:not(:has(img)) [data-testid="stButtonLabel"],
    section[data-testid="stSidebar"] div[data-testid="stContainer"] .stButton > button:not(:has(img)) [data-testid="baseButton-label"] {
        white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
        line-height: 1.35 !important; height: auto !important; min-height: 0 !important;
        align-items: flex-start !important; word-break: break-word !important;
    }
    section[data-testid="stSidebar"] hr {
        display: none !important; height: 0 !important; margin: 0 !important;
        padding: 0 !important; border: 0 !important;
    }
    section[data-testid="stSidebar"] .yukt-sidebar-navitem .stButton > button {
        width: 100%; text-align: left; justify-content: flex-start;
        background: transparent !important; border: 0 !important; box-shadow: none !important;
        padding: 0.5rem 0.6rem !important; font-weight: 650 !important;
        color: var(--text-color) !important; display: flex !important;
        align-items: center !important; gap: 0.55rem !important;
    }
    section[data-testid="stSidebar"] .yukt-sidebar-navitem .stButton > button:hover {
        background: color-mix(in srgb, var(--primary-color) 10%, transparent) !important;
    }
    section[data-testid="stSidebar"] .yukt-sidebar-navitem .stButton > button:has(img) {
        border-bottom: 1px solid var(--yukt-chrome-border) !important;
        padding-bottom: 0.45rem !important; margin-bottom: 0.2rem !important;
    }
    section[data-testid="stSidebar"] .yukt-sidebar-section-title {
        padding: 0.5rem 0.6rem; font-weight: 650; color: var(--text-color);
        opacity: 0.95; user-select: none; display: flex; align-items: center; gap: 0.55rem;
    }

    button[data-testid="collapsedControl"],
    button[aria-label="Open sidebar"],
    button[aria-label="Close sidebar"],
    button[title="Open sidebar"],
    button[title="Close sidebar"] {
        position: fixed !important;
        top: calc(var(--yukt-header-height) + 6px) !important;
        left: 12px !important;
        z-index: 1000000 !important; width: 36px !important; height: 36px !important;
        padding: 0 !important; border-radius: 999px !important;
        display: grid !important; place-items: center !important; box-shadow: none !important;
    }
    button[data-testid="collapsedControl"] svg,
    button[aria-label="Open sidebar"] svg,
    button[aria-label="Close sidebar"] svg,
    button[title="Open sidebar"] svg,
    button[title="Close sidebar"] svg { display: none !important; }
    button[data-testid="collapsedControl"]::before,
    button[aria-label="Open sidebar"]::before,
    button[aria-label="Close sidebar"]::before,
    button[title="Open sidebar"]::before,
    button[title="Close sidebar"]::before {
        content: "+"; font-size: 22px; line-height: 1; font-weight: 700;
        color: var(--text-color); opacity: 0.9; transform-origin: 50% 50%;
        transition: transform 120ms ease, opacity 120ms ease;
    }
    button[aria-label="Close sidebar"]::before,
    button[title="Close sidebar"]::before { transform: rotate(45deg); opacity: 0.95; }
    html.yukt-sidebar-open button[data-testid="collapsedControl"],
    html.yukt-sidebar-open button[aria-label="Open sidebar"],
    html.yukt-sidebar-open button[aria-label="Close sidebar"],
    html.yukt-sidebar-open button[title="Open sidebar"],
    html.yukt-sidebar-open button[title="Close sidebar"] { display: none !important; }

    .yukt-sidebar-plus-icon {
        font-size: 22px; line-height: 1; font-weight: 700; color: var(--text-color);
        opacity: 0.92; transform-origin: 50% 50%;
        transition: transform 120ms ease, opacity 120ms ease;
        user-select: none; pointer-events: none;
    }
    .yukt-sidebar-plus-icon.yukt-rot { transform: rotate(45deg); opacity: 0.97; }

    .yukt-chat-row { display: flex; width: 100%; margin-bottom: 0.15rem; }
    .yukt-row-user { justify-content: flex-end; }
    .yukt-row-assistant { justify-content: flex-start; }
    /* Streaming assistant (st.fragment): pull bubble up so it reads as one thread, not a second card. */
    .yukt-rag-stream-slot { margin-top: -0.4rem; margin-bottom: 0; }
    .yukt-rag-stream-slot .yukt-chat-row { margin-bottom: 0.08rem; }
    .yukt-bubble {
        max-width: min(88%, 760px); padding: 0.7rem 1rem;
        border: 1px solid rgba(148, 163, 184, 0.35);
        background: var(--secondary-background-color); color: var(--text-color);
        text-align: left; line-height: 1.5; word-wrap: break-word;
    }
    .yukt-bubble p { margin: 0 0 0.45rem 0; }
    .yukt-bubble p:last-child { margin-bottom: 0; }
    .yukt-progress-fadeout {
        display: inline-block;
        opacity: 1;
        animation: yuktProgressFadeOut 0.45s ease-out forwards;
    }
    @keyframes yuktProgressFadeOut {
        from { opacity: 1; transform: translateY(0); }
        to { opacity: 0; transform: translateY(-2px); }
    }
    .yukt-bubble ul, .yukt-bubble ol { margin: 0.35rem 0 0.35rem 1.1rem; padding-left: 0.2rem; }
    .yukt-bubble code {
        background: color-mix(in srgb, var(--text-color) 8%, transparent);
        padding: 0.1rem 0.35rem; border-radius: 0.25rem; font-size: 0.92em;
    }
    .yukt-bubble pre {
        background: color-mix(in srgb, var(--text-color) 6%, transparent);
        padding: 0.6rem 0.75rem; border-radius: 0.5rem;
        overflow-x: auto; margin: 0.4rem 0;
    }
    .yukt-bubble pre code { background: transparent; padding: 0; }
    .yukt-bubble-user {
        border-radius: 1rem 1rem 0.35rem 1rem;
        background: color-mix(in srgb, var(--primary-color) 14%, var(--secondary-background-color));
        border-color: color-mix(in srgb, var(--primary-color) 30%, rgba(148, 163, 184, 0.35));
    }
    .yukt-bubble-assistant {
        border-radius: 1rem 1rem 1rem 0.35rem;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .yukt-bubble-assistant a.yukt-page-ref,
    .yukt-bubble-assistant a[href*="yukt_pdf_open="],
    .yukt-bubble-assistant span.yukt-pdf-deeplink,
    .yukt-bubble-assistant span.yukt-inline-page-open,
    .yukt-bubble-assistant span.yukt-inline-video-time-open {
        color: #0d9488;
        text-decoration: underline;
        font-weight: 500;
        cursor: pointer;
    }
    .yukt-bubble-assistant .yukt-source-chip {
        display: inline-block;
        margin: 2px 0 6px 0;
        padding: 0.18rem 0.48rem;
        border: 1px solid color-mix(in srgb, #0d9488 18%, rgba(148, 163, 184, 0.45));
        border-radius: 0.45rem;
        background: color-mix(in srgb, #0d9488 6%, var(--secondary-background-color));
        font-size: 0.86rem;
        line-height: 1.3;
        cursor: pointer;
        user-select: none;
    }
    .yukt-bubble-assistant .yukt-source-chip:hover {
        background: color-mix(in srgb, #0d9488 10%, var(--secondary-background-color));
    }
    .yukt-msg-ts-user, .yukt-msg-ts-assistant {
        font-size: 0.78rem; opacity: 0.72; margin-top: -2px; margin-bottom: 0.45rem;
        color: var(--text-color);
    }
    .yukt-msg-ts-user { text-align: right; }
    .yukt-msg-ts-assistant { text-align: left; }

    header[data-testid="stHeader"], .stApp > header {
        display: none !important; height: 0 !important; min-height: 0 !important;
        max-height: 0 !important; margin: 0 !important; padding: 0 !important;
        overflow: hidden !important; border: none !important; visibility: hidden !important;
    }
    #MainMenu { visibility: hidden; }
    header [data-testid="stMainMenu"] { display: none; }
    div[data-testid="stMainMenu"] { display: none; }
    button[kind="header"] { display: none; }
    [data-testid="stSidebarCollapseButton"] { display: none !important; }
    [data-testid="stToolbar"] { display: none !important; }
    [data-testid="stStatusWidget"] { display: none !important; }

    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .stButton > button {
        justify-content: center !important;
        padding-left: 0.35rem !important; padding-right: 0.35rem !important;
        text-align: center !important;
    }
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .stButton > button > div,
    html.yukt-sidebar-nav-collapsed section[data-testid="stSidebar"] .stButton > button [data-testid="baseButton-label"] {
        justify-content: center !important; text-align: center !important;
    }
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stSidebarContent"] > div[data-testid="stVerticalBlock"]
        > div[data-testid="stElementContainer"]:nth-child(n+5) .stButton > button,
    html.yukt-sidebar-nav-expanded section[data-testid="stSidebar"]
        div[data-testid="stSidebarContent"] > div > div[data-testid="stVerticalBlock"]
        > div[data-testid="stElementContainer"]:nth-child(n+5) .stButton > button {
        min-height: 44px !important; height: auto !important;
        width: 100% !important; max-width: 100% !important; box-sizing: border-box !important;
        text-align: left; justify-content: flex-start !important;
        padding-left: 10px !important; font-size: 14px;
    }

    .yukt-pdf-title {
        margin: 0 !important; padding: 0 !important;
        font-size: 1.05rem; font-weight: 600; line-height: 1.25;
    }
    div[data-testid="stDialog"] .yukt-pdf-title { position: relative; top: -4px; }
    div[data-testid="stDialog"] div[role="dialog"] div[style*="padding: 1.5rem"] {
        padding-top: 0.5rem !important;
    }
    /* Solid surface behind retrieved figures (avoids any host/theme bleed-through). */
    [data-testid="stImage"],
    [data-testid="stImage"] img {
        background: #ffffff !important;
    }

    .stChatInput { position: sticky; bottom: 0; }
    [data-testid="stChatInput"] {
        border-radius: 1.35rem !important;
        border: 1px solid var(--yukt-chrome-border) !important;
        background: var(--secondary-background-color) !important;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06) !important;
    }
    [data-testid="stChatInput"] textarea,
    [data-testid="stChatInput"] [data-baseweb="textarea"] textarea {
        border-radius: 1.35rem !important;
    }
    /* Keep Streamlit audio recorder mounted for browser capture, but hidden from UI. */
    .st-key-yukt_stt_audio {
        position: fixed !important;
        left: -9999px !important;
        top: -9999px !important;
        width: 1px !important;
        height: 1px !important;
        opacity: 0 !important;
        pointer-events: none !important;
        overflow: hidden !important;
        z-index: -1 !important;
    }
    .st-key-yukt_stt_mic_btn {
        position: fixed !important;
        right: 150px !important;
        bottom: 65px !important;
        z-index: 1004 !important;
        margin: 0 !important;
    }
    .st-key-yukt_stt_mic_btn .stButton > button {
        width: 32px !important;
        min-width: 32px !important;
        height: 32px !important;
        border-radius: 999px !important;
        border: 0 !important;
        background: transparent !important;
        padding: 0 !important;
        box-shadow: none !important;
        font-size: 14px !important;
        line-height: 1 !important;
        color: #0f172a !important;
    }
    .st-key-yukt_stt_mic_btn .stButton > button:hover {
        background: transparent !important;
        color: #020617 !important;
    }
    .st-key-yukt_stt_mic_btn .stButton > button [data-testid="stMarkdownContainer"] {
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: 100% !important;
        height: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    .st-key-yukt_stt_mic_btn .stButton > button img,
    .st-key-yukt_stt_mic_btn .stButton > button [data-testid="stMarkdownContainer"] img {
        width: 20px !important;
        min-width: 20px !important;
        max-width: 20px !important;
        height: 20px !important;
        min-height: 20px !important;
        max-height: 20px !important;
        object-fit: contain !important;
        display: block !important;
        transform: scale(1.25) !important;
        transform-origin: center center !important;
    }
    .yukt-stt-listening-indicator {
        position: fixed;
        right: 104px;
        bottom: 37px;
        z-index: 1003;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid var(--yukt-chrome-border);
        background: var(--secondary-background-color);
        font-size: 0.8rem;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
    }
    .yukt-stt-pulse-dot {
        width: 9px;
        height: 9px;
        border-radius: 999px;
        background: #ef4444;
        box-shadow: 0 0 0 rgba(239, 68, 68, 0.45);
        animation: yuktMicPulse 1.25s infinite;
    }
    @keyframes yuktMicPulse {
        0% {
            transform: scale(0.96);
            box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.48);
        }
        70% {
            transform: scale(1);
            box-shadow: 0 0 0 8px rgba(239, 68, 68, 0);
        }
        100% {
            transform: scale(0.96);
            box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
        }
    }
    div[data-testid="stDialog"] div[role="dialog"] { position: relative; }
    div[data-testid="stDialog"] button[aria-label="Close"],
    div[data-testid="stDialog"] button[title="Close"] {
        position: absolute !important; top: 6px !important; right: 8px !important;
        width: 40px !important; height: 40px !important; padding: 0 !important;
        border-radius: 999px !important; display: grid !important;
        place-items: center !important; z-index: 10 !important;
    }
    div[data-testid="stDialog"] button[aria-label="Close"] svg,
    div[data-testid="stDialog"] button[title="Close"] svg {
        width: 15px !important; height: 15px !important;
    }
</style>
"""
