"""Versioned prompt files, loaded and rendered safely (NFR-MAINT-03, NFR-REPRO-01).

A prompt is a Markdown file named `<agent>.<purpose>.v<N>.md` with a YAML front-matter block that
lists its variables. A change to a prompt is a new file with the next version; old versions stay
so a recorded run can be replayed with the words it used. `versions.lock.json` holds a hash for
every shipped prompt, and `lock_problems` reports a file whose content changed under the same
version, which is how "every change bumps the version" is enforced.

Feed data (scheme names, file contents) is untrusted. The `untrusted` template filter wraps it in
a `<data>` tag that it cannot close, and prompts tell the model that tag contents are data, never
instructions (design §13).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, TemplateSyntaxError, meta
from jinja2.sandbox import SandboxedEnvironment

from lineageheal.core.errors import AgentError

DEFAULT_DIRECTORY = Path(__file__).parent / "prompts"
LOCK_FILE = "versions.lock.json"

_NAME = re.compile(
    r"^(?P<agent>[a-z][a-z0-9_]*)\.(?P<purpose>[a-z][a-z0-9_]*)\.v(?P<version>[1-9][0-9]*)\.md$"
)
_TAG_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_CLOSING_TAG = re.compile(r"</\s*data", re.IGNORECASE)

# Enough for a log excerpt or a model's SQL; a longer value is cut so one row cannot flood a prompt.
MAX_UNTRUSTED_CHARACTERS = 4000


class PromptError(AgentError):
    """A prompt file is malformed, missing, or was rendered with the wrong variables."""


def untrusted(value: object, name: str, limit: int = MAX_UNTRUSTED_CHARACTERS) -> str:
    """Wrap feed data in a `<data>` tag so the model reads it as data, not as instructions.

    A closing tag inside the value is broken so the data cannot end its own delimiter, and a
    value past `limit` characters is cut with a marker.
    """
    if not _TAG_NAME.match(name):
        raise PromptError(f"{name!r} is not a valid data tag name")
    text = str(value)
    if len(text) > limit:
        text = text[:limit] + " [truncated]"
    text = _CLOSING_TAG.sub("<\\/data", text)
    return f'<data name="{name}">\n{text}\n</data>'


def _environment() -> SandboxedEnvironment:
    # Undefined names must fail loudly: an empty string in a prompt is a silent wrong answer.
    environment = SandboxedEnvironment(
        undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True
    )
    environment.filters["untrusted"] = untrusted
    return environment


@dataclass(frozen=True, slots=True)
class Prompt:
    """One version of one prompt."""

    agent: str
    purpose: str
    version: int
    variables: tuple[str, ...]
    description: str
    template: str
    sha256: str

    @property
    def id(self) -> str:
        """`<agent>.<purpose>.v<N>`: what a run records as the prompt it used."""
        return f"{self.agent}.{self.purpose}.v{self.version}"

    def render(self, **values: object) -> str:
        """Fill the template; the values must be exactly the declared variables."""
        missing = set(self.variables) - values.keys()
        unexpected = values.keys() - set(self.variables)
        if missing or unexpected:
            raise PromptError(
                f"{self.id}: missing variables {sorted(missing)}, unexpected {sorted(unexpected)}"
            )
        return _environment().from_string(self.template).render(**values)


class PromptRegistry:
    """Every prompt in one folder, read and checked at construction."""

    def __init__(self, directory: Path = DEFAULT_DIRECTORY) -> None:
        """Load and validate every prompt file in `directory`.

        Raises:
            PromptError: A file name, front matter or template is malformed, or two files claim
                the same prompt and version.
        """
        self._directory = directory
        self._prompts: dict[str, Prompt] = {}
        for path in sorted(directory.glob("*.md")):
            prompt = _load(path)
            self._prompts[prompt.id] = prompt

    def get(self, agent: str, purpose: str, version: int | None = None) -> Prompt:
        """A prompt by name: the newest version, or exactly `version` when pinned.

        Raises:
            PromptError: No such prompt or version.
        """
        found = [p for p in self._prompts.values() if (p.agent, p.purpose) == (agent, purpose)]
        if version is not None:
            found = [p for p in found if p.version == version]
        if not found:
            wanted = f"{agent}.{purpose}" + (f".v{version}" if version else "")
            raise PromptError(f"no prompt {wanted!r}; have {sorted(self._prompts)}")
        return max(found, key=lambda p: p.version)

    def ids(self) -> list[str]:
        """Every prompt id, sorted."""
        return sorted(self._prompts)

    def lock_problems(self, lock: Mapping[str, str]) -> list[str]:
        """Where the files and the lock disagree, as sentences a person can act on."""
        problems = []
        for prompt_id, prompt in sorted(self._prompts.items()):
            recorded = lock.get(prompt_id)
            if recorded is None:
                problems.append(
                    f'{prompt_id} is not in the lock; add "{prompt_id}": "{prompt.sha256}"'
                )
            elif recorded != prompt.sha256:
                next_id = f"{prompt.agent}.{prompt.purpose}.v{prompt.version + 1}"
                problems.append(
                    f"{prompt_id} changed after it was locked; restore it and put the change in "
                    f"a new file ({next_id})"
                )
        problems += [
            f"{i} is in the lock but its file is gone"
            for i in sorted(set(lock) - set(self._prompts))
        ]
        return problems


def _load(path: Path) -> Prompt:
    named = _NAME.match(path.name)
    if named is None:
        raise PromptError(f"{path.name}: expected <agent>.<purpose>.v<N>.md in lower case")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    front, body = _split_front_matter(text, path.name)
    declared = front.get("variables")
    if not isinstance(declared, list) or not all(isinstance(v, str) for v in declared):
        raise PromptError(f"{path.name}: front matter needs `variables`, a list (empty if none)")
    try:
        used = meta.find_undeclared_variables(_environment().parse(body))
    except TemplateSyntaxError as error:
        raise PromptError(
            f"{path.name}: template error on line {error.lineno}: {error.message}"
        ) from error
    if used != set(declared):
        raise PromptError(
            f"{path.name}: front matter lists {sorted(declared)} "
            f"but the template uses {sorted(used)}"
        )
    return Prompt(
        agent=named["agent"],
        purpose=named["purpose"],
        version=int(named["version"]),
        variables=tuple(declared),
        description=str(front.get("description", "")),
        template=body,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _split_front_matter(text: str, name: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise PromptError(f"{name}: the file must start with a `---` front-matter block")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise PromptError(f"{name}: the front-matter block is not closed with `---`")
    try:
        front = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as error:
        raise PromptError(f"{name}: front matter is not valid YAML") from error
    if not isinstance(front, dict):
        raise PromptError(f"{name}: front matter must be a mapping")
    return front, text[end + 5 :]
