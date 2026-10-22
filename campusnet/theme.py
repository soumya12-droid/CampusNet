"""The dark engineering theme: one palette for Plotly figures and the app's CSS.

`.streamlit/config.toml` holds the same values for Streamlit's own widgets (tests check the two agree).
Orange and amber are the identity; green and red are semantic only (healthy or delivered, failed or
dropped). Surfaces are flat, borders thin, and nothing glows.
"""

# surfaces
BG_APP = "#111315"  # main application background
BG_SIDEBAR = "#090A0B"  # sidebar: the darkest surface
SURFACE_SIDEBAR = "#101214"  # inputs and raised controls inside the sidebar
PANEL = "#191C1F"  # main panels
ELEVATED = "#202428"  # raised surfaces: hover, selected rows, code
BORDER = "#30353A"  # borders and dividers

# text
TEXT = "#ECE9E2"
TEXT_MUTED = "#A3A7AA"
TEXT_ON_ACCENT = "#17120A"  # dark text for use on orange or amber fills

# accents and semantics
ORANGE = "#F29A38"  # primary accent
AMBER = "#F2C14E"  # secondary accent
AMBER_DIM = "#B8903B"  # amber for moderate emphasis (e.g. a queue half full)
GREEN = "#83B889"  # healthy, up, delivered
RED = "#D96C61"  # failed, dropped
LINK = "#777F87"  # neutral link

# device markers: shape carries the type, color only supports it
CLIENT = "#8D949B"
SWITCH = "#D3AD45"
ROUTER = "#C5C8CA"
SERVER = GREEN

# chart series for comparing runs: warm and neutral, so they never read as semantic green or red
SERIES = ["#F29A38", "#F2C14E", "#C69C6D", "#A3A7AA", "#B96F1E"]
UNRESOLVED = "#5B6269"

# drop reasons: red family, told apart by lightness
DROP_REASON_COLORS = {"queue_full": RED, "link_down": "#B5493F", "router_down": "#8C3A33", "no_route": "#E6A59E"}

FONT = "'Segoe UI', 'Inter', system-ui, -apple-system, 'Helvetica Neue', Arial, sans-serif"
MONO = "'Cascadia Mono', 'JetBrains Mono', Consolas, 'SF Mono', Menlo, monospace"


_CSS = """
.block-container { padding: 1.2rem 1.6rem 2rem 1.6rem; max-width: 1600px; }
h1, h2, h3, h4, h5 { letter-spacing: 0; color: @TEXT@; }
hr { border-color: @BORDER@; }

/* identity: the page title is the one orange heading; section labels are quiet */
.cn-title { font-size: 1.45rem; font-weight: 650; margin: 0; line-height: 1.2; color: @ORANGE@; }
.cn-sub { color: @TEXT_MUTED@; font-size: 0.88rem; margin-top: 0.1rem; }
.cn-status { color: @TEXT_MUTED@; font-size: 0.85rem; text-align: right; padding-top: 0.35rem; }
.cn-section { font-size: 0.78rem; font-weight: 650; text-transform: uppercase; letter-spacing: 0.05em;
              color: @TEXT_MUTED@; margin: 1.0rem 0 0.35rem 0; padding-bottom: 0.25rem; border-bottom: 1px solid @BORDER@; }
.cn-step { font-size: 0.8rem; color: @TEXT_MUTED@; margin: 0.15rem 0 0.35rem 0; }
[data-testid="stSidebar"] .cn-title { color: @TEXT@; }
[data-testid="stSidebar"] .cn-section { margin-top: 0.9rem; }
[data-testid="stSidebar"] { border-right: 1px solid @BORDER@; }

/* text details */
code, [data-testid="stMarkdownContainer"] code, [data-testid="stCaptionContainer"] code {
  background: @ELEVATED@; color: @TEXT@; border-radius: 3px; padding: 0.05rem 0.3rem; font-family: @MONO@; font-size: 0.85em; }
[data-testid="stMetricValue"] { font-size: 1.45rem; color: @TEXT@; }
[data-testid="stMetricLabel"], [data-testid="stCaptionContainer"] { color: @TEXT_MUTED@; }

/* surfaces: flat, thin borders, small radii */
div[data-testid="stVerticalBlockBorderWrapper"] { background: @PANEL@; border-color: @BORDER@; border-radius: 4px; }
[data-testid="stExpander"] details { border-color: @BORDER@; border-radius: 4px; background: @PANEL@; }
[data-testid="stDataFrame"] { border: 1px solid @BORDER@; border-radius: 4px; }
[data-testid="stAlert"] { background: @PANEL@; border: 1px solid @BORDER@; border-left: 3px solid @AMBER@; border-radius: 4px; color: @TEXT@; }
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) { border-left-color: @RED@; }
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) { border-left-color: @GREEN@; }
[data-testid="stAlertContainer"], [data-testid="stAlertContainer"] * { background: transparent; color: @TEXT@; }

/* buttons: orange for the primary action only; hover changes colour or border, nothing else */
button[data-testid="stBaseButton-primary"] { background: @ORANGE@; border: 1px solid @ORANGE@; color: @TEXT_ON_ACCENT@; font-weight: 600; }
button[data-testid="stBaseButton-primary"]:hover { background: @AMBER@; border-color: @AMBER@; color: @TEXT_ON_ACCENT@; }
button[data-testid="stBaseButton-secondary"], [data-testid="stDownloadButton"] button { background: @PANEL@; border: 1px solid @BORDER@; color: @TEXT@; }
button[data-testid="stBaseButton-secondary"]:hover, [data-testid="stDownloadButton"] button:hover { border-color: @ORANGE@; color: @TEXT@; }
button:disabled { opacity: 0.42; }
.st-key-jump_Failures button { color: @RED@; border-color: @RED_BORDER@; }
.st-key-jump_Failures button:hover:not(:disabled) { border-color: @RED@; color: @RED@; }
.st-key-jump_Deliveries button { color: @GREEN@; border-color: @GREEN_BORDER@; }
.st-key-jump_Deliveries button:hover:not(:disabled) { border-color: @GREEN@; color: @GREEN@; }

/* segmented controls (analysis views, inspector sections): the selected one has a thin orange border and amber text */
button[data-testid="stBaseButton-segmented_control"] { background: @PANEL@; border: 1px solid @BORDER@; color: @TEXT_MUTED@; }
button[data-testid="stBaseButton-segmented_control"]:hover { border-color: @ORANGE@; color: @TEXT@; }
button[data-testid="stBaseButton-segmented_controlActive"] { background: @ORANGE_TINT@; border: 1px solid @ORANGE@; color: @AMBER@; font-weight: 600; }
"""


def css() -> str:
    """The app's CSS, with palette tokens filled in. Streamlit's own widgets are themed in .streamlit/config.toml."""
    tokens = {
        "TEXT": TEXT, "TEXT_MUTED": TEXT_MUTED, "TEXT_ON_ACCENT": TEXT_ON_ACCENT, "BORDER": BORDER, "PANEL": PANEL,
        "ELEVATED": ELEVATED, "ORANGE": ORANGE, "AMBER": AMBER, "GREEN": GREEN, "RED": RED, "MONO": MONO,
        "RED_BORDER": rgba(RED, 0.5), "GREEN_BORDER": rgba(GREEN, 0.5), "ORANGE_TINT": rgba(ORANGE, 0.14),
    }
    out = _CSS
    for name, value in tokens.items():
        out = out.replace(f"@{name}@", value)
    return out


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 2.x contrast ratio between two #rrggbb colors."""
    def luminance(hex_color: str) -> float:
        h = hex_color.lstrip("#")
        channels = []
        for i in (0, 2, 4):
            c = int(h[i:i + 2], 16) / 255
            channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    a, b = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (a + 0.05) / (b + 0.05)
