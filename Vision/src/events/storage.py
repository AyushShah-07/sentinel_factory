"""
JSON storage utilities for the Sentinel Factory event system.
"""

import json
import os


# ============================================================
# ENSURE JSON FILE
# ============================================================

def ensure_json_file(path):
    """
    Create an empty JSON list file if it does not exist.
    """

    directory = os.path.dirname(path)

    if directory:
        os.makedirs(
            directory,
            exist_ok=True,
        )

    if not os.path.exists(path):

        with open(
            path,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                [],
                file,
                indent=2,
            )


# ============================================================
# APPEND JSON
# ============================================================

def append_json(path, record):
    """
    Append one record to a JSON list.

    A temporary file is used so that the original file
    is replaced only after the new content is written.
    """

    try:

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

            if not isinstance(
                data,
                list,
            ):
                data = []

    except (
        FileNotFoundError,
        json.JSONDecodeError,
    ):

        data = []

    data.append(record)

    temp_path = path + ".tmp"

    with open(
        temp_path,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            indent=2,
        )

    os.replace(
        temp_path,
        path,
    )