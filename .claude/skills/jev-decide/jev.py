#!/usr/bin/env python3
"""Sends typed questions to TypeSafe's Jev via OpenRouter and prints its typed answers, flagging the uncertain ones."""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.14"
TYPES = {"noul", "choice", "score"}
EXIT_NO_KEY, EXIT_BAD_ANSWER, EXIT_HTTP, EXIT_BAD_INPUT = 3, 2, 4, 5


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses redirects so the bearer key is never replayed to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tracked_by_git(path):
    """True when git tracks the file, so a committed .env can't swap in someone else's key."""
    try:
        return subprocess.run(["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def load_key():
    """Reads OPENROUTER_API_KEY from the environment, else from the nearest untracked .env up from the cwd."""
    if os.environ.get("OPENROUTER_API_KEY"):
        return os.environ["OPENROUTER_API_KEY"]
    for folder in [Path.cwd(), *Path.cwd().parents]:
        env = folder / ".env"
        if env.is_file() and not tracked_by_git(env):
            for line in env.read_text(encoding="utf-8").splitlines():
                name, _, value = line.strip().partition("=")
                if name.removeprefix("export ").strip() == "OPENROUTER_API_KEY" and value.strip():
                    return value.strip().strip("'\"")
    return None


def valid_questions(questions):
    return (isinstance(questions, dict) and questions
            and all(isinstance(q, dict) and q.get("type") in TYPES for q in questions.values()))


def valid_answer(answer, kind):
    if not isinstance(answer, dict) or answer.get("type") != kind:
        return False
    if kind == "noul":
        return isinstance(answer.get("noul"), (int, float))
    return answer.get(kind) is not None and isinstance(answer.get("confidence", 0), (int, float))


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
    parser.add_argument("--verbose", action="store_true", help="print the HTTP error body, which may echo the request")
    args = parser.parse_args()

    try:
        body = json.load(sys.stdin)
        questions = body["questions"]
    except (ValueError, KeyError, TypeError) as e:
        print(f"Bad input, expected {{state, questions}} JSON: {e}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if not valid_questions(questions):
        print(f"Bad input: questions must be a non-empty object of {{type: {'|'.join(sorted(TYPES))}, ...}}", file=sys.stderr)
        return EXIT_BAD_INPUT
    body["model"] = args.model
    if args.dry_run:
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return 0

    key = load_key()
    if not key:
        print("No OPENROUTER_API_KEY in the environment or an untracked .env: decide without Jev and say so.", file=sys.stderr)
        return EXIT_NO_KEY

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as resp:
            result = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = f": {e.read().decode(errors='replace')[:500]}" if args.verbose else " (rerun with --verbose for the body)"
        print(f"Jev HTTP {e.code}{detail}", file=sys.stderr)
        return EXIT_HTTP
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Jev unreachable: {e}", file=sys.stderr)
        return EXIT_HTTP
    except ValueError:
        print("Jev returned a non-JSON response", file=sys.stderr)
        return EXIT_BAD_ANSWER

    answers = result.get("answers") if isinstance(result, dict) else None
    if not isinstance(answers, dict):
        print("Jev returned no answers object", file=sys.stderr)
        return EXIT_BAD_ANSWER
    for qid, question in questions.items():
        answer = answers.get(qid)
        if not valid_answer(answer, question["type"]):
            print(f"Jev returned no valid '{question['type']}' answer for '{qid}'", file=sys.stderr)
            return EXIT_BAD_ANSWER
        answer["uncertain"] = uncertain(answer, args.min_confidence, args.noul_band)

    print(json.dumps({
        "model": result.get("model"),
        "answers": answers,
        "cost_usd": (result.get("usage") or {}).get("cost"),
        "id": result.get("id"),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
