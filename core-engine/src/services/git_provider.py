"""
GitHub App integration.

Phase 4. Authenticates as a GitHub App, not as a user: the App's permissions
are visible in the repository settings, revocable in one click, and scoped to
the repositories an admin explicitly selected. A personal access token has
none of those properties and inherits everything its owner can do.

## Write posture

The App holds `contents: write` and `pull_requests: write`, which is what
opening a PR requires. Every write here goes to a **new branch**, and merging
is always a human action:

* commits land on `cloudoptimizer/…` branches, never on a default branch
* nothing force-pushes
* nothing merges, approves, or closes
* branch names are deterministic, so re-running is idempotent rather than
  producing a new branch per run

The narrow scope is the point. An integration that could merge its own pull
requests would need a trust argument this one does not have to make.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"

# GitHub caps App JWTs at 10 minutes. Nine leaves room for clock skew between
# this host and GitHub, which is the usual cause of "'exp' claim timeout".
JWT_TTL_SECONDS = 9 * 60

# Installation tokens last an hour. Refreshed early so a long-running job
# cannot have one expire mid-operation.
TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60

BRANCH_PREFIX = "cloudoptimizer"


class GitProviderError(Exception):
    pass


class GitProviderUnavailable(GitProviderError):
    """Not configured. Distinct so callers can degrade rather than fail."""


@dataclass
class PullRequest:
    number: int
    url: str
    branch: str
    created: bool  # False when an open PR for this branch already existed


class GitHubApp:
    def __init__(
        self,
        app_id: str | None = None,
        private_key: str | None = None,
        installation_id: str | None = None,
    ):
        self.app_id = (app_id or os.environ.get("GITHUB_APP_ID", "")).strip()
        self.installation_id = (
            installation_id or os.environ.get("GITHUB_APP_INSTALLATION_ID", "")
        ).strip()
        self._private_key = private_key or self._load_private_key()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @staticmethod
    def _load_private_key() -> str:
        """
        Accept the key inline or as a path.

        A path is preferable: an inline PEM in an environment variable shows
        up in process listings, crash dumps, and any tool that prints the
        environment.
        """
        raw = os.environ.get("GITHUB_APP_PRIVATE_KEY", "").strip()
        if not raw:
            return ""
        if "BEGIN" in raw and "PRIVATE KEY" in raw:
            # Some secret stores collapse newlines into literal \n.
            return raw.replace("\\n", "\n")
        # A relative path is resolved against the .env file's directory
        # first, then against the working directory. Without the first, the
        # same configuration works from the repository root and fails from
        # core-engine/, which is where the CLI runs.
        searched = []
        env_dir = os.environ.get("CLOUDOPTIMIZER_ENV_DIR")
        candidates = []
        if env_dir and not pathlib.Path(raw).is_absolute():
            candidates.append(pathlib.Path(env_dir) / raw)
        candidates.append(pathlib.Path(raw).expanduser())

        for candidate in candidates:
            searched.append(str(candidate))
            if candidate.exists():
                return candidate.read_text()

        raise GitProviderError(
            f"GITHUB_APP_PRIVATE_KEY points at {raw!r}, which was not found. "
            f"Looked in: {', '.join(searched)}. Provide a readable path or "
            "the PEM contents."
        )

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.installation_id and self._private_key)

    def describe(self) -> dict[str, Any]:
        return {
            "app_id": self.app_id or None,
            "installation_id": self.installation_id or None,
            "configured": self.configured,
            "private_key_present": bool(self._private_key),
        }

    # -- auth --------------------------------------------------------------

    def _app_jwt(self) -> str:
        import jwt  # PyJWT, with the cryptography extra for RS256

        now = int(time.time())
        return jwt.encode(
            {
                # Backdated: GitHub rejects a token whose iat is in the future
                # by even a second, and clocks drift.
                "iat": now - 60,
                "exp": now + JWT_TTL_SECONDS,
                "iss": self.app_id,
            },
            self._private_key,
            algorithm="RS256",
        )

    def installation_token(self) -> str:
        """Cached installation token, refreshed before it expires."""
        if not self.configured:
            raise GitProviderUnavailable(
                "GitHub App is not configured. Set GITHUB_APP_ID, "
                "GITHUB_APP_PRIVATE_KEY, and GITHUB_APP_INSTALLATION_ID."
            )
        if self._token and time.time() < self._token_expires_at:
            return self._token

        payload = self._request(
            "POST",
            f"/app/installations/{self.installation_id}/access_tokens",
            token=self._app_jwt(),
        )
        self._token = payload["token"]
        self._token_expires_at = time.time() + 3600 - TOKEN_REFRESH_MARGIN_SECONDS
        return self._token

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict | None = None,
        allow_404: bool = False,
    ) -> Any:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token or self.installation_token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cloudoptimizer",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and allow_404:
                return None
            detail = self._detail(exc)
            if exc.code in (401, 403):
                raise GitProviderError(
                    f"GitHub refused the request ({exc.code}). Check the App's "
                    f"permissions and that it is installed on this repository. {detail}"
                )
            raise GitProviderError(f"GitHub {method} {path} failed ({exc.code}): {detail}")
        except Exception as exc:
            raise GitProviderError(f"GitHub {method} {path} failed: {exc}") from exc

    @staticmethod
    def _detail(exc: urllib.error.HTTPError) -> str:
        try:
            return json.loads(exc.read().decode()).get("message", "")[:300]
        except Exception:
            return ""

    # -- repository operations --------------------------------------------

    def list_repositories(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/installation/repositories")
        return [
            {
                "full_name": repo["full_name"],
                "default_branch": repo["default_branch"],
                "private": repo["private"],
            }
            for repo in payload.get("repositories", [])
        ]

    def get_file(self, repo: str, path: str, ref: str | None = None) -> dict | None:
        """Return {content, sha} for a file, or None when it does not exist."""
        query = f"?ref={ref}" if ref else ""
        payload = self._request(
            "GET", f"/repos/{repo}/contents/{path}{query}", allow_404=True
        )
        if payload is None or payload.get("type") != "file":
            return None
        return {
            "content": base64.b64decode(payload["content"]).decode("utf-8", "replace"),
            "sha": payload["sha"],
            "path": payload["path"],
        }

    def default_branch(self, repo: str) -> str:
        return self._request("GET", f"/repos/{repo}")["default_branch"]

    # -- pull request review ----------------------------------------------
    #
    # The Phase 4 direction was writing PRs. This is the reverse: reading one
    # that a human (or an agent) already opened and reporting what it will
    # reach. The auth, retry, and error handling are identical, so it belongs
    # on the same client.

    def get_pull_request(self, repo: str, number: int) -> dict | None:
        payload = self._request(
            "GET", f"/repos/{repo}/pulls/{number}", allow_404=True
        )
        if payload is None:
            return None
        return {
            "number": payload["number"],
            "title": payload.get("title"),
            "base_sha": payload["base"]["sha"],
            "head_sha": payload["head"]["sha"],
            "base_ref": payload["base"]["ref"],
            "head_ref": payload["head"]["ref"],
            "state": payload.get("state"),
            "draft": payload.get("draft", False),
        }

    def pull_request_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        """
        Every file the pull request touches.

        Paginated at 100 per page and followed to the end: a migration PR that
        touches 300 manifests is exactly the change most worth analysing, and
        silently reading the first hundred would report a blast radius that is
        confidently too small.
        """
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._request(
                "GET", f"/repos/{repo}/pulls/{number}/files?per_page=100&page={page}"
            )
            if not batch:
                break
            files.extend({
                "path": entry["filename"],
                "status": entry["status"],
                "previous_path": entry.get("previous_filename"),
                "additions": entry.get("additions", 0),
                "deletions": entry.get("deletions", 0),
            } for entry in batch)
            if len(batch) < 100:
                break
            page += 1
            if page > 30:  # 3000 files; beyond this the diff is not reviewable
                break
        return files

    def create_check_run(
        self,
        repo: str,
        head_sha: str,
        *,
        name: str,
        conclusion: str,
        title: str,
        summary: str,
        text: str | None = None,
    ) -> dict:
        """
        Publish a check run against the head commit.

        A check run rather than a status: it carries a title, a markdown body,
        and its own tab, so the reasoning travels with the verdict. A red mark
        with no explanation of what it will break gets overridden and then
        ignored.
        """
        return self._request(
            "POST",
            f"/repos/{repo}/check-runs",
            body={
                "name": name,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "output": {
                    "title": title[:255],
                    "summary": summary[:65535],
                    **({"text": text[:65535]} if text else {}),
                },
            },
        )

    def upsert_pull_request_comment(
        self, repo: str, number: int, body: str, *, marker: str
    ) -> dict:
        """
        Post a comment, replacing this bot's previous one.

        ``marker`` is an HTML comment embedded in the body. Without it, every
        push appends another analysis and the review turns into a wall of
        near-identical bot comments -- which is how teams end up muting the
        integration that was supposed to help them.
        """
        existing = self._request(
            "GET", f"/repos/{repo}/issues/{number}/comments?per_page=100"
        ) or []
        for comment in existing:
            if marker in (comment.get("body") or ""):
                return self._request(
                    "PATCH",
                    f"/repos/{repo}/issues/comments/{comment['id']}",
                    body={"body": body},
                )
        return self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments", body={"body": body}
        )

    def _branch_head(self, repo: str, branch: str) -> str | None:
        ref = self._request(
            "GET", f"/repos/{repo}/git/ref/heads/{branch}", allow_404=True
        )
        return ref["object"]["sha"] if ref else None

    def ensure_branch(self, repo: str, branch: str, base: str) -> str:
        """
        Create ``branch`` from ``base`` if absent; return its head SHA.

        An existing branch is reused rather than reset. Re-running a fix must
        not discard a commit a reviewer pushed onto the branch.
        """
        existing = self._branch_head(repo, branch)
        if existing:
            return existing
        base_sha = self._branch_head(repo, base)
        if not base_sha:
            raise GitProviderError(f"Base branch {base!r} not found in {repo}")
        self._request(
            "POST",
            f"/repos/{repo}/git/refs",
            body={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        return base_sha

    def put_file(
        self,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str | None = None,
    ) -> dict:
        """
        Create or update one file on a branch.

        ``sha`` is the blob being replaced. Omitting it on an existing file is
        rejected by GitHub, which is the desired behaviour: it prevents
        overwriting a change made since the content was read.
        """
        body = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        return self._request("PUT", f"/repos/{repo}/contents/{path}", body=body)

    def find_open_pull_request(self, repo: str, branch: str) -> dict | None:
        owner = repo.split("/")[0]
        results = self._request(
            "GET", f"/repos/{repo}/pulls?state=open&head={owner}:{branch}"
        )
        return results[0] if results else None

    def open_pull_request(
        self,
        repo: str,
        *,
        branch: str,
        base: str,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> PullRequest:
        """
        Open a PR for a branch, or return the existing open one.

        Idempotent on purpose: a scheduled job that runs daily must not open a
        new pull request every day for a fix nobody has merged yet.
        """
        existing = self.find_open_pull_request(repo, branch)
        if existing:
            return PullRequest(
                number=existing["number"],
                url=existing["html_url"],
                branch=branch,
                created=False,
            )

        created = self._request(
            "POST",
            f"/repos/{repo}/pulls",
            body={"title": title, "body": body, "head": branch, "base": base},
        )
        if labels:
            try:
                self._request(
                    "POST",
                    f"/repos/{repo}/issues/{created['number']}/labels",
                    body={"labels": labels},
                )
            except GitProviderError as exc:
                # Labels must exist in the repository first. Not worth failing
                # a pull request over.
                log.info("Could not apply labels to PR: %s", exc)

        return PullRequest(
            number=created["number"],
            url=created["html_url"],
            branch=branch,
            created=True,
        )


def branch_name(kind: str, identifier: str) -> str:
    """
    Deterministic branch name.

    Same input yields the same branch, so a re-run updates the existing pull
    request instead of opening a second one for the same finding.
    """
    safe = "".join(
        char if char.isalnum() or char in "-._" else "-" for char in identifier
    ).strip("-").lower()
    return f"{BRANCH_PREFIX}/{kind}/{safe}"[:240]
