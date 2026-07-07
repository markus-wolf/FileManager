"""Norton Commander theme — the classic late-80s DOS palette, from CGA.

CGA color reference (the real hardware values NC 3.0 rendered with):
  blue #0000AA   cyan #00AAAA   bright cyan #55FFFF   white #FFFFFF
  yellow #FFFF55 (marked files)  red #AA0000  bright green #55FF55

Mapping to Textual's semantic slots:
  background/surface  → NC panel blue
  panel               → menu-bar cyan (header, chrome)
  primary/secondary   → cyan / bright cyan (borders, tab highlight)
  accent              → yellow (marks — exactly NC's marked-file color)
  footer variables    → the black F-key bar with cyan chips
"""
from textual.theme import Theme

CGA_BLUE        = "#0000AA"
CGA_CYAN        = "#00AAAA"
CGA_CYAN_BRIGHT = "#55FFFF"
CGA_WHITE       = "#FFFFFF"
CGA_YELLOW      = "#FFFF55"
CGA_GREEN       = "#55FF55"
CGA_BLACK       = "#000000"

# Personal palette override: warnings/errors use bright orange instead of
# CGA red — red-on-blue is hard to see with the owner's color vision.
# Not in the 1989 palette; readability wins. Tune here if needed.
ALERT_ORANGE    = "#FFA500"

NORTON_THEME = Theme(
    name="norton-commander",
    primary=CGA_CYAN,
    secondary=CGA_CYAN_BRIGHT,
    accent=CGA_YELLOW,
    warning=ALERT_ORANGE,
    error=ALERT_ORANGE,
    success=CGA_GREEN,
    foreground=CGA_CYAN_BRIGHT,     # NC file text: bright cyan on blue
    background=CGA_BLUE,
    surface=CGA_BLUE,
    panel=CGA_CYAN,
    dark=True,
    variables={
        # F-key bar: black strip, white numbers, black-on-cyan labels
        "footer-background": CGA_BLACK,
        "footer-key-foreground": CGA_WHITE,
        "footer-key-background": CGA_BLACK,
        "footer-description-foreground": CGA_BLACK,
        "footer-description-background": CGA_CYAN,
        "block-cursor-foreground": CGA_BLACK,
        "block-cursor-background": CGA_CYAN,   # NC cursor bar
        "datatable--header-cursor": CGA_CYAN,
    },
)
