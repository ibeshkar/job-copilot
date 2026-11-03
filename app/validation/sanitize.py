# What: escaping for user text before Streamlit draws it: `md()` for st.markdown / st.caption /
#       st.info / labels (Markdown), `html_text()` for the few places that build HTML with
#       unsafe_allow_html=True or st.html.
# Why: Streamlit renders Markdown in most text elements. Without escaping, a job title like
#      "![x](https://evil.example/?leak)" would load an image from another site, "[Click](…)"
#      would become a link, ":red[…]" would recolour the page and "$…$" would turn into LaTeX.
#      Escaping makes user text show exactly as typed. (Validation decides what may be saved;
#      escaping decides how saved text is shown. Both are needed: old data and LLM-extracted
#      titles were never validated.)
# Used by: app/main.py, app/ui/components.py and the pages in app/ui/pages/.

import html
import re

# Characters that start Markdown, Streamlit directives (":red[…]", ":smile:"), LaTeX ("$") or
# HTML anywhere in a line. A backslash before ASCII punctuation shows the plain mark (CommonMark).
_INLINE_SPECIAL = re.compile(r"([\\`*_{}\[\]<>~:$&|#])")
# Markers that only mean something at the start of a line: lists ("- ", "+ ", "1. "),
# setext headings ("===").
_LINE_START = re.compile(r"^(\s*)([-+]|\d+[.)]|=)(?=\s|=|$)", re.MULTILINE)


def md(text: object) -> str:
    """User text made safe for Markdown: shown literally, never as formatting, links or images.

    Example: md("![x](https://evil.example)") → "!\\[x\\](https://evil.example)", which shows the
    text instead of loading an image.
    """
    escaped = _INLINE_SPECIAL.sub(r"\\\1", "" if text is None else str(text))
    return _LINE_START.sub(lambda m: m.group(1) + m.group(2)[:-1] + "\\" + m.group(2)[-1], escaped)


def html_text(text: object) -> str:
    """User text made safe inside HTML (tags and quotes escaped; newlines become <br>)."""
    return html.escape("" if text is None else str(text), quote=True).replace("\n", "<br>")
