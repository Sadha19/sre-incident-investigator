import re


LOG_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} "
    r"\d{2}:\d{2}:\d{2}) "
    r"(?P<level>INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<message>.*)$"
)


def parse_log_line(line):
    """
    Parse a single log line.

    Returns:
        dict: Parsed log information
        None: If the line is invalid
    """

    line = line.strip()

    match = LOG_PATTERN.match(line)

    if not match:
        return None

    return {
        "timestamp": match.group("timestamp"),
        "level": match.group("level"),
        "message": match.group("message"),
    }


def parse_log_file(file_path):
    """
    Read and parse a complete log file.
    """

    logs = []

    with open(file_path, "r", encoding="utf-8") as file:

        for line in file:

            parsed = parse_log_line(line)

            if parsed:
                logs.append(parsed)

    return logs
