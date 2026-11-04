import re

# Completely unreadable
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CREDIT_CARD_REGEX = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")


def redact_text(text: str) -> str:
    text = EMAIL_REGEX.sub("[REDACTED]", text)
    text = SSN_REGEX.sub("[REDACTED]", text)
    text = CREDIT_CARD_REGEX.sub("[REDACTED]", text)
    return text


class StreamingPIIRedactor:
    def __init__(self, buffer_size: int = 60):
        self.buffer = ""
        self.buffer_size = buffer_size

    def process_chunk(self, chunk: str) -> str:
        combined = self.buffer + chunk
        redacted = redact_text(combined)

        if len(redacted) > self.buffer_size:
            emit_len = len(redacted) - self.buffer_size
            to_emit = redacted[:emit_len]
            self.buffer = redacted[emit_len:]
            return to_emit
        else:
            self.buffer = redacted
            return ""

    def flush(self) -> str:
        to_emit = self.buffer
        self.buffer = ""
        return to_emit
