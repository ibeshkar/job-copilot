# What: the central input validation package: validators.py (one rule per kind of input),
#       models.py (one Pydantic model per form or service input), sanitize.py (escaping user
#       text before it is drawn as Markdown or HTML).
# Why: every page, service and storage function checks input with the same rules; see
#      docs/DECISIONS.md D91 and docs/SECURITY.md.
