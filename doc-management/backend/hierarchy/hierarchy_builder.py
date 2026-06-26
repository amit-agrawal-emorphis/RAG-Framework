import re
from typing import List, Dict

#NUMERIC_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[\.)]?\s+(.+)$")
NUMERIC_HEADING = re.compile(r"^(\d+(?:\.\d+)*)(?:[.)\:])?\s+(.+)$")


def normalize_section_number(number: str) -> str:
    number = number.strip().replace(" ", "")
    number = number.rstrip(".")
    return number


def update_hierarchy_stack(
    hierarchy_stack: List[Dict], number: str, title: str
) -> List[Dict]:
    number = normalize_section_number(number)
    parts = number.split(".")

    level = len(parts)

    # keep only parent levels
    new_stack = hierarchy_stack[: level - 1]

    # append current section
    new_stack.append(
        {
            "number": number,
            "title": title,
            "depth": level,
        }
    )

    return new_stack