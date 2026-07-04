"""Turn staged local fixes into GitHub PR suggestion review comments."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable

VERSION = "0.1.0"


@dataclass
class Hunk:
    path: str
    old_start: int
    new_start: int
    old_count: int
    new_count: int
    removed: list[str]
    added: list[str]
    lines: list[str]
    binary: bool = False
    rename: bool = False
    new_file: bool = False
    deleted_file: bool = False


@dataclass
class Suggestion:
    path: str
    line: int
    body: str
    preview: str


@dataclass
class Skip:
    path: str
    line: int | None
    reason: str


def run(cmd: list[str], *, input_text: str | None = None) -> str:
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise RuntimeError(f"{' '.join(cmd)} failed: {detail}")
    return proc.stdout


def parse_count(raw: str | None) -> int:
    return int(raw) if raw else 1


def parse_unified_diff(text: str) -> list[Hunk]:
    hunks: list[Hunk] = []
    path = ""
    flags = {"binary": False, "rename": False, "new_file": False, "deleted_file": False}
    current: Hunk | None = None

    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = None
            parts = line.split()
            path = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else ""
            flags = {"binary": False, "rename": False, "new_file": False, "deleted_file": False}
        elif line.startswith("Binary files "):
            flags["binary"] = True
        elif line.startswith("rename from ") or line.startswith("rename to "):
            flags["rename"] = True
        elif line.startswith("new file mode "):
            flags["new_file"] = True
        elif line.startswith("deleted file mode "):
            flags["deleted_file"] = True
        elif line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@ "):
            header = line.split("@@", 2)[1].strip()
            old_part, new_part = header.split()[:2]
            old_bits = old_part[1:].split(",", 1)
            new_bits = new_part[1:].split(",", 1)
            current = Hunk(
                path=path,
                old_start=int(old_bits[0]),
                old_count=parse_count(old_bits[1] if len(old_bits) > 1 else None),
                new_start=int(new_bits[0]),
                new_count=parse_count(new_bits[1] if len(new_bits) > 1 else None),
                removed=[],
                added=[],
                lines=[],
                **flags,
            )
            hunks.append(current)
        elif current and line.startswith("+") and not line.startswith("+++"):
            current.added.append(line[1:])
            current.lines.append(line)
        elif current and line.startswith("-") and not line.startswith("---"):
            current.removed.append(line[1:])
            current.lines.append(line)
        elif current and line.startswith(" "):
            current.lines.append(line)

    return hunks


def pr_added_lines(hunks: Iterable[Hunk]) -> dict[str, dict[int, int]]:
    positions: dict[str, dict[int, int]] = {}
    seen: dict[tuple[str, int], int] = {}
    position = 0
    for hunk in hunks:
        new_line = hunk.new_start
        old_line = hunk.old_start
        for raw in hunk_to_lines(hunk):
            position += 1
            if raw.startswith("+"):
                positions.setdefault(hunk.path, {})[new_line] = position
                seen[(hunk.path, new_line)] = seen.get((hunk.path, new_line), 0) + 1
                new_line += 1
            elif raw.startswith("-"):
                old_line += 1
            else:
                new_line += 1
                old_line += 1

    for path, line in [key for key, count in seen.items() if count > 1]:
        positions.get(path, {}).pop(line, None)
    return positions


def hunk_to_lines(hunk: Hunk) -> list[str]:
    return hunk.lines


def render_suggestion(lines: list[str]) -> str:
    return "```suggestion\n" + "\n".join(lines) + "\n```"


def map_suggestions(staged: list[Hunk], pr_positions: dict[str, dict[int, int]], limit: int) -> tuple[list[Suggestion], list[Skip]]:
    suggestions: list[Suggestion] = []
    skips: list[Skip] = []

    for hunk in staged:
        line = hunk.new_start
        if hunk.binary:
            skips.append(Skip(hunk.path, line, "binary file"))
        elif hunk.rename:
            skips.append(Skip(hunk.path, line, "rename not supported"))
        elif hunk.new_file:
            skips.append(Skip(hunk.path, line, "new file not supported"))
        elif hunk.deleted_file:
            skips.append(Skip(hunk.path, line, "delete not supported"))
        elif hunk.path not in pr_positions:
            skips.append(Skip(hunk.path, line, "file not in PR diff"))
        elif not hunk.added:
            skips.append(Skip(hunk.path, line, "empty suggestion"))
        elif len(hunk.added) > limit:
            skips.append(Skip(hunk.path, line, "hunk too large"))
        elif line not in pr_positions[hunk.path]:
            skips.append(Skip(hunk.path, line, "line not in PR diff"))
        else:
            suggestions.append(
                Suggestion(
                    path=hunk.path,
                    line=line,
                    body=render_suggestion(hunk.added),
                    preview=hunk.added[0] if len(hunk.added) == 1 else f"{len(hunk.added)} lines",
                )
            )

    return suggestions, skips


def preview(pr: str, suggestions: list[Suggestion], skips: list[Skip]) -> str:
    lines = [f"gh-suggest: preview for #{pr}", ""]
    lines.append(f"Will post {len(suggestions)} suggestion{'s' if len(suggestions) != 1 else ''}:")
    for item in suggestions:
        lines.append(f"- {item.path}:{item.line} {item.preview}")
    if skips:
        lines.extend(["", f"Skipped {len(skips)} hunk{'s' if len(skips) != 1 else ''}:"])
        for item in skips:
            where = f"{item.path}:{item.line}" if item.line else item.path
            lines.append(f"- {where}: {item.reason}")
    return "\n".join(lines)


def github_pr_context(pr: str, repo: str | None, runner: Callable[..., str] = run) -> dict[str, str]:
    cmd = ["gh", "pr", "view", pr, "--json", "id,number,repository"]
    if repo:
        cmd.extend(["--repo", repo])
    data = json.loads(runner(cmd))
    return {
        "id": data["id"],
        "number": str(data["number"]),
        "owner": data["repository"]["owner"]["login"],
        "name": data["repository"]["name"],
    }


def github_submit_review(
    context: dict[str, str],
    suggestions: list[Suggestion],
    body: str,
    runner: Callable[..., str] = run,
) -> None:
    review_id = json.loads(
        runner(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                "query=mutation($pullRequestId:ID!,$body:String!){addPullRequestReview(input:{pullRequestId:$pullRequestId,event:PENDING,body:$body}){pullRequestReview{id}}}",
                "-f",
                f"pullRequestId={context['id']}",
                "-f",
                f"body={body}",
            ]
        )
    )["data"]["addPullRequestReview"]["pullRequestReview"]["id"]

    for item in suggestions:
        runner(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                "query=mutation($reviewId:ID!,$body:String!,$path:String!,$line:Int!){addPullRequestReviewThread(input:{pullRequestReviewId:$reviewId,body:$body,path:$path,line:$line,side:RIGHT}){thread{id}}}",
                "-f",
                f"reviewId={review_id}",
                "-f",
                f"body={item.body}",
                "-f",
                f"path={item.path}",
                "-F",
                f"line={item.line}",
            ]
        )

    runner(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            "query=mutation($reviewId:ID!,$body:String!){submitPullRequestReview(input:{pullRequestReviewId:$reviewId,event:COMMENT,body:$body}){pullRequestReview{id}}}",
            "-f",
            f"reviewId={review_id}",
            "-f",
            f"body={body}",
        ]
    )


def require_tools() -> None:
    missing = [tool for tool in ("git", "gh") if not shutil.which(tool)]
    if missing:
        raise RuntimeError("missing required tool: " + ", ".join(missing))
    run(["gh", "auth", "status"])


def read_diff(args: argparse.Namespace) -> str:
    cmd = ["git", "diff", "--cached", "--unified=0", "--no-ext-diff"]
    staged = run(cmd)
    if not args.include_unstaged:
        return staged
    unstaged = run(["git", "diff", "--unified=0", "--no-ext-diff"])
    return staged + "\n" + unstaged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Post staged local fixes as GitHub PR suggestions.")
    parser.add_argument("pr", nargs="?", help="Pull request number or URL")
    parser.add_argument("--repo", help="GitHub repository, for example owner/name")
    parser.add_argument("--body", default="Suggested from local test run.", help="Review body")
    parser.add_argument("--limit", type=int, default=30, help="Maximum replacement lines per suggestion")
    parser.add_argument("--include-unstaged", action="store_true", help="Also include unstaged changes")
    parser.add_argument("--dry-run", action="store_true", help="Print exactly what would post")
    parser.add_argument("--yes", action="store_true", help="Post without prompting")
    parser.add_argument("--version", action="version", version=f"gh-suggest {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pr:
        build_parser().error("PR number or URL is required")

    try:
        require_tools()
        staged = parse_unified_diff(read_diff(args))
        if not staged:
            print("gh-suggest: nothing posted\n\n- No staged changes found.\n- Run `git add <file>` first, or use `--include-unstaged`.", file=sys.stderr)
            return 1

        pr_cmd = ["gh", "pr", "diff", args.pr, "--patch"]
        if args.repo:
            pr_cmd.extend(["--repo", args.repo])
        suggestions, skips = map_suggestions(staged, pr_added_lines(parse_unified_diff(run(pr_cmd))), args.limit)
        print(preview(args.pr, suggestions, skips))
        if not suggestions:
            print("\ngh-suggest: nothing posted", file=sys.stderr)
            return 1
        if args.dry_run:
            return 0
        if not args.yes and input("\nPost this review? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("gh-suggest: cancelled")
            return 1
        github_submit_review(github_pr_context(args.pr, args.repo), suggestions, args.body)
        print(f"\ngh-suggest: posted review on #{args.pr}\n\n{len(suggestions)} suggestions posted\n{len(skips)} hunks skipped")
        return 0
    except RuntimeError as exc:
        print(f"gh-suggest: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
