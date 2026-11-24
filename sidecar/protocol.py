"""Generate local meeting minutes from an already diarized transcript."""
import argparse
import http.client
import json
import sys
import urllib.request
from pathlib import Path


def clean_protocol(text: str) -> str:
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def generate_protocol(transcript: str, model: str, endpoint: str) -> str:
    prompt = (
        "Ты готовишь протокол русской деловой встречи по транскрипту. "
        "Используй только сведения из транскрипта, ничего не выдумывай. "
        "Если ответственный или срок не назван, напиши «не указано». "
        "Выведи только готовый протокол без рассуждений и анализа. "
        "Разделы: «Краткое резюме», «Ключевые решения», "
        "«Задачи» (задача, ответственный, срок), «Открытые вопросы».\n\n"
        f"Транскрипт:\n{transcript}"
    )
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2},
    }).encode()
    request = urllib.request.Request(
        f"{endpoint.rstrip('/')}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.load(response)
    message = result.get("message") if isinstance(result, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("Ollama вернула некорректный ответ")
    return clean_protocol(message["content"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Составить протокол по сохранённой расшифровке")
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--ollama-model", default="qwen3.5:4b")
    args = parser.parse_args()
    try:
        result = generate_protocol(args.transcript.read_text(encoding="utf-8"), args.ollama_model, "http://127.0.0.1:11434")
        if not result:
            raise ValueError("Ollama вернула пустой протокол")
    except (OSError, http.client.HTTPException, KeyError, ValueError) as error:
        sys.exit(f"Не удалось составить протокол: {error}")
    print(result)


if __name__ == "__main__":
    main()
