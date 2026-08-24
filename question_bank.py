"""Remote question-bank loading with a last-known-good local cache."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_REMOTE_QUESTION_BANK_URL = (
    "https://raw.githubusercontent.com/KONpiGG/astrbot_plugin_soupai/master/"
    "network_soupai.json"
)
MAX_REMOTE_BYTES = 2 * 1024 * 1024
MAX_TEXT_LENGTH = 8_000


class RemoteQuestionBank:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path

    @staticmethod
    def parse(payload: bytes):
        """Return validated question tuples and counts from a SoupAI-style JSON list."""
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("远程题库必须是 JSON 数组")

        questions = []
        seen = set()
        invalid_count = 0
        duplicate_count = 0
        for index, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                invalid_count += 1
                continue
            puzzle = item.get("puzzle")
            answer = item.get("answer")
            if not isinstance(puzzle, str) or not isinstance(answer, str):
                invalid_count += 1
                continue
            puzzle = puzzle.strip()
            answer = answer.strip()
            if (
                not puzzle
                or not answer
                or len(puzzle) > MAX_TEXT_LENGTH
                or len(answer) > MAX_TEXT_LENGTH
            ):
                invalid_count += 1
                continue
            fingerprint = hashlib.sha256(f"{puzzle}\0{answer}".encode()).hexdigest()
            if fingerprint in seen:
                duplicate_count += 1
                continue
            seen.add(fingerprint)
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                title = "网络海龟汤"
            else:
                title = title.strip()[:200]
            difficulty = item.get("difficulty", 3)
            if not isinstance(difficulty, int) or isinstance(difficulty, bool):
                difficulty = 3
            difficulty = max(1, min(difficulty, 5))
            questions.append(
                (
                    puzzle,
                    answer,
                    {
                        "id": f"N{index:03d}",
                        "title": title,
                        "difficulty": difficulty,
                        "tags": ["网络题库"],
                        "source": "remote",
                    },
                )
            )
        return questions, {
            "valid": len(questions),
            "invalid": invalid_count,
            "duplicates": duplicate_count,
        }

    @staticmethod
    def download(url: str, timeout_seconds: int) -> bytes:
        if not url.startswith("https://"):
            raise ValueError("远程题库地址必须使用 HTTPS")
        request = Request(url, headers={"User-Agent": "AstrBot-TurtleSoup/1.0"})
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_REMOTE_BYTES + 1)
        if len(payload) > MAX_REMOTE_BYTES:
            raise ValueError("远程题库超过 2 MiB 限制")
        return payload

    def load_cache(self):
        if not self.cache_path.exists():
            return [], {"valid": 0, "invalid": 0, "duplicates": 0}
        return self.parse(self.cache_path.read_bytes())

    def save_cache(self, payload: bytes) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=self.cache_path.parent, delete=False
        ) as temporary_file:
            temporary_file.write(payload)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, self.cache_path)

    def cache_age_seconds(self):
        if not self.cache_path.exists():
            return None
        return max(0, int(time.time() - self.cache_path.stat().st_mtime))
