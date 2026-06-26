import re

NUMERIC_HEADING = re.compile(
    r"^\s*((?:\d+\.?)+)\s+(.+?)\s*$"
)

def detect_heading(text: str):
    text = text.strip()

    match = NUMERIC_HEADING.match(text)
    if not match:
        return False, "", ""

    number = match.group(1).rstrip(".")
    title = match.group(2).strip()

    # reject single large numbers (UI menu items)
    if "." not in number and int(number) > 15:
        return False, "", ""

    # reject headings that look like sentences
    if len(title.split()) > 12:
        return False, "", ""

    return True, number, title