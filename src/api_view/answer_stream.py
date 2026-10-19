"""Decode only the explicit answer tool's JSON string, never narration or tool arguments."""
import json
import re
from api_view.output_protocol import public_answer

class BusinessAnswerStream:
    def __init__(self):
        self.raw = ""
        self.sent = 0
        self.answer = ""

    def feed(self, fragment):
        self.raw += fragment
        start = re.match(r'\s*\{\s*"answer"\s*:\s*"', self.raw)
        if not start:
            return ""
        position = start.end()
        end = position
        while end < len(self.raw):
            character = self.raw[end]
            if character == '"':
                break
            if character == "\\":
                if end + 1 >= len(self.raw):
                    break
                length = 6 if self.raw[end + 1] == 'u' else 2
                if end + length > len(self.raw):
                    break
                end += length
            else:
                end += 1
        try:
            text = json.loads('"' + self.raw[position:end] + '"')
        except (ValueError, TypeError):
            return ""
        # Retain the unfinished line: identifiers must never leak across chunk boundaries.
        boundary = text.rfind('\n') + 1
        try:
            full = json.loads(self.raw)
        except ValueError:
            full = None
        if isinstance(full, dict) and isinstance(full.get('answer'), str):
            boundary = len(text)
        self.answer = public_answer(text[:boundary])
        delta = self.answer[self.sent:]
        self.sent = len(self.answer)
        return delta

    def finish(self):
        return self.feed("")
