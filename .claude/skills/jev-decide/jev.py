#!/usr/bin/env python3
"""Sends typed questions to TypeSafe's Jev via OpenRouter and prints its typed answers, flagging the uncertain ones."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"
EXIT_NO_KEY, EXIT_BAD_ANSWER, EXIT_HTTP = 3, 2, 4


def load_key():
    """Reads OPENROUTER_API_KEY from the environment, else from the nearest .env up from the cwd."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    for folder in [Path.cwd(), *Path.cwd().parents]:
        env = folder / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                name, _, value = line.strip().partition("=")
                if name.removeprefix("export ").strip() == "OPENROUTER_API_KEY" and value.strip():
                    return value.strip().strip("'\"")
    return None


def uncertain(answer, min_confidence, noul_band):
    if answer["type"] == "noul":
        return noul_band[0] < answer["noul"] < noul_band[1]
    return answer.get("confidence", 0) < min_confidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--min-confidence", type=float, default=0.6, help="choice/score below this are uncertain")
    parser.add_argument("--noul-band", type=float, nargs=2, default=(0.2, 0.8), help="noul inside this band is uncertain")
    parser.add_argument("--dry-run", action="store_true", help="print the exact body that would leave the machine, send nothing")
    args = parser.parse_args()

    body = json.load(sys.stdin)
    body["model"] = args.model
    if args.dry_run:
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return 0

    key = load_key()
    if not key:
        print("No OPENROUTER_API_KEY in the environment or a .env file: decide without Jev and say so.", file=sys.stderr)
        return EXIT_NO_KEY

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"Jev HTTP {e.code}: {e.read().decode(errors='replace')[:500]}", file=sys.stderr)
        return EXIT_HTTP
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Jev unreachable: {e}", file=sys.stderr)
        return EXIT_HTTP

    answers = result.get("answers", {})
    for qid, question in body["questions"].items():
        answer = answers.get(qid)
        if not answer or answer.get("type") != question["type"]:
            print(f"Jev returned no valid '{question['type']}' answer for '{qid}'", file=sys.stderr)
            return EXIT_BAD_ANSWER
        answer["uncertain"] = uncertain(answer, args.min_confidence, args.noul_band)

    print(json.dumps({
        "model": result.get("model"),
        "answers": answers,
        "cost_usd": result.get("usage", {}).get("cost"),
        "id": result.get("id"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
