"""GitHub access for the agents and the approval flow: branches, pull requests and issues."""

from lineageheal.vcs.auth import AppAuth
from lineageheal.vcs.client import GitHubClient, Issue, PullRequest
from lineageheal.vcs.factory import build_github_client
from lineageheal.vcs.issue import IssueContent, render_issue
from lineageheal.vcs.pull_request import PullRequestContent, render_pull_request

__all__ = [
    "AppAuth",
    "GitHubClient",
    "Issue",
    "IssueContent",
    "PullRequest",
    "PullRequestContent",
    "build_github_client",
    "render_issue",
    "render_pull_request",
]
