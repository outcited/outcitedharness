#!/usr/bin/env python3
"""Fail-closed smoke and concurrency checks for the local GLM-5.3 endpoint."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import re
import time
import urllib.error
import urllib.request


def request_json(
    base_url: str,
    api_key: str,
    path: str,
    *,
    body: dict | None = None,
    timeout: int = 900,
) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"{path} returned HTTP {error.code}: {detail}") from error


def completion_body(
    prompt: str,
    *,
    model: str = "glm-5.3",
    max_tokens: int = 256,
    reasoning_effort: str = "low",
) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "reasoning_effort": reasoning_effort,
    }


def final_content(response: dict) -> str:
    return str(response["choices"][0]["message"].get("content") or "")


def benchmark(
    base_url: str, api_key: str, model: str, concurrency: int
) -> tuple[int, float]:
    prompt = "Write the integers 1 through 100, one integer per line, with no prose."
    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(
                request_json,
                base_url,
                api_key,
                "/v1/chat/completions",
                body=completion_body(prompt, model=model),
            )
            for _ in range(concurrency)
        ]
        responses = [future.result() for future in futures]
    elapsed = time.monotonic() - started
    expected = list(range(1, 101))
    for response in responses:
        assert response["choices"][0].get("finish_reason") == "stop", response
        integers = [
            int(value)
            for value in re.findall(r"(?m)^\s*(\d+)\s*$", final_content(response))
        ]
        assert integers == expected, integers
    tokens = sum(
        int(response.get("usage", {}).get("completion_tokens", 0))
        for response in responses
    )
    return tokens, elapsed


def tokenized_message_count(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
) -> int:
    response = request_json(
        base_url,
        api_key,
        "/tokenize",
        body={"model": model, "messages": messages},
        timeout=1800,
    )
    if "count" in response:
        return int(response["count"])
    for field in ("tokens", "token_ids"):
        if isinstance(response.get(field), list):
            return len(response[field])
    raise RuntimeError(f"/tokenize response has no token count: {response.keys()}")


def long_context_messages(
    total_filler_lines: int, qualification_nonce: str
) -> list[dict[str, str]]:
    filler = (
        "Routine archive row: amber cedar quartz seven. "
        "This row contains no request or instruction.\n"
    )
    secrets = {
        0: "The first retention code is ORBIT-7319.",
        2: "The middle retention code is MAPLE-4826.",
        5: "The final retention code is RAVEN-9054.",
    }
    per_turn, remainder = divmod(total_filler_lines, 6)
    messages: list[dict[str, str]] = []
    for turn in range(6):
        line_count = per_turn + (1 if turn < remainder else 0)
        marker = secrets.get(turn, "There is no retention code in this exchange.")
        nonce_line = (
            f"Qualification archive nonce: {qualification_nonce}.\n"
            if turn == 0
            else ""
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    nonce_line
                    + f"Exchange {turn + 1} archival material follows. {marker}\n"
                    + filler * line_count
                ),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"Acknowledged archival exchange {turn + 1}.",
            }
        )
    messages.append(
        {
            "role": "user",
            "content": (
                "This is exchange 7. Return the three retention codes from "
                "exchanges 1, 3, and 6 in chronological order. Return only "
                "the codes, one per line."
            ),
        }
    )
    return messages


def qualify_long_context(
    base_url: str, api_key: str, model: str, target_tokens: int
) -> tuple[int, float]:
    qualification_nonce = str(time.time_ns())
    empty_count = tokenized_message_count(
        base_url, api_key, model, long_context_messages(0, qualification_nonce)
    )
    sample_lines = 600
    sample_count = tokenized_message_count(
        base_url,
        api_key,
        model,
        long_context_messages(sample_lines, qualification_nonce),
    )
    tokens_per_line = (sample_count - empty_count) / sample_lines
    if tokens_per_line <= 0:
        raise RuntimeError("could not estimate long-context filler token density")

    total_lines = max(1, int((target_tokens - empty_count) / tokens_per_line))
    messages = long_context_messages(total_lines, qualification_nonce)
    prompt_tokens = tokenized_message_count(
        base_url, api_key, model, messages
    )
    for _ in range(3):
        if int(target_tokens * 0.985) <= prompt_tokens <= target_tokens:
            break
        scale = min(0.999, target_tokens / prompt_tokens)
        total_lines = max(1, int(total_lines * scale))
        if prompt_tokens < int(target_tokens * 0.985):
            total_lines += max(
                1, int((target_tokens - prompt_tokens) / tokens_per_line)
            )
        messages = long_context_messages(total_lines, qualification_nonce)
        prompt_tokens = tokenized_message_count(
            base_url, api_key, model, messages
        )

    assert prompt_tokens <= target_tokens, (prompt_tokens, target_tokens)
    minimum_prompt_tokens = int(target_tokens * 0.97)
    assert prompt_tokens >= minimum_prompt_tokens, (prompt_tokens, target_tokens)

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 2048,
        "reasoning_effort": "max",
    }
    started = time.monotonic()
    response = request_json(
        base_url,
        api_key,
        "/v1/chat/completions",
        body=body,
        timeout=7200,
    )
    elapsed = time.monotonic() - started
    assert response["choices"][0].get("finish_reason") == "stop", response
    answer = final_content(response)
    normalized = re.sub(r"[^a-z0-9]", "", answer.lower())
    for code in ("ORBIT-7319", "MAPLE-4826", "RAVEN-9054"):
        assert re.sub(r"[^a-z0-9]", "", code.lower()) in normalized, answer
    used = int(response.get("usage", {}).get("prompt_tokens", prompt_tokens))
    assert minimum_prompt_tokens <= used <= target_tokens, (used, target_tokens)
    return used, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="glm-5.3")
    parser.add_argument(
        "--key-file", default=str(pathlib.Path.home() / ".secrets" / "glm53-key")
    )
    parser.add_argument(
        "--long-context-tokens",
        type=int,
        default=0,
        help="run a seven-exchange retention test near this prompt-token count",
    )
    args = parser.parse_args()
    api_key = pathlib.Path(args.key_file).read_text().strip()

    models = request_json(args.base_url, api_key, "/v1/models")
    model_ids = {item["id"] for item in models.get("data", [])}
    assert args.model in model_ids, model_ids
    print("PASS model listing")

    answer = final_content(
        request_json(
            args.base_url,
            api_key,
            "/v1/chat/completions",
            body=completion_body(
                "A bat and ball cost $1.10 total. The bat costs $1.00 more than "
                "the ball. What does the ball cost? Show the algebra.",
                model=args.model,
            ),
        )
    )
    normalized = answer.lower().replace(" ", "")
    assert "$0.05" in normalized or "5cents" in normalized, answer
    print("PASS coherence")

    tool_body = completion_body(
        "Call get_weather for Seattle, Washington. Do not answer directly.",
        model=args.model,
    )
    tool_body.update(
        {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather for a city.",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ],
            "tool_choice": "required",
        }
    )
    tool_response = request_json(
        args.base_url, api_key, "/v1/chat/completions", body=tool_body
    )
    calls = tool_response["choices"][0]["message"].get("tool_calls") or []
    assert calls and calls[0]["function"]["name"] == "get_weather", tool_response
    arguments = json.loads(calls[0]["function"]["arguments"])
    assert "seattle" in arguments["city"].lower(), arguments
    print("PASS tool calling")

    for concurrency in (1, 2):
        tokens, elapsed = benchmark(
            args.base_url, api_key, args.model, concurrency
        )
        rate = tokens / elapsed
        assert tokens > 0 and rate > 0
        print(
            f"PASS concurrency={concurrency} tokens={tokens} "
            f"elapsed={elapsed:.2f}s aggregate={rate:.2f} tok/s"
        )
    if args.long_context_tokens:
        used, elapsed = qualify_long_context(
            args.base_url, api_key, args.model, args.long_context_tokens
        )
        print(
            f"PASS long-context-seven-exchange prompt_tokens={used} "
            f"elapsed={elapsed:.2f}s reasoning=max"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
