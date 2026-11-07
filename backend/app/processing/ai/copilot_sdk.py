"""GitHub Copilot SDK provider (https://docs.github.com/en/copilot/how-tos/copilot-sdk).

Used as a plain completion endpoint: every session has **no tools** (built-in or custom), so
the agent runtime cannot read files, run commands or browse; it can only answer the prompt.
Usage is billed to the Copilot plan of the GitHub token supplied.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from app.processing.ai.provider import Completion, ProviderError


def _deny_all(request, invocation):
    """Second line of defence: even if a tool were somehow offered, never allow it."""
    from copilot.generated.rpc import PermissionDecisionReject

    return PermissionDecisionReject()


class CopilotSdkProvider:
    name = "github-copilot-sdk"

    def __init__(self, github_token: str, model: str, timeout_seconds: float = 120) -> None:
        if not github_token:
            raise ProviderError("A GitHub token with Copilot access is required")
        self.model = model
        self._token = github_token
        self._timeout = timeout_seconds
        self._client = None
        self._lock = asyncio.Lock()

    async def _ensure_client(self):
        async with self._lock:
            if self._client is None:
                from copilot import CopilotClient

                client = CopilotClient(github_token=self._token, log_level="error")
                try:
                    await client.start()
                except Exception as exc:
                    raise ProviderError(f"Could not start Copilot runtime: {exc}") from exc
                self._client = client
            return self._client

    async def complete(self, system: str, prompt: str) -> Completion:
        client = await self._ensure_client()
        session = None
        try:
            session = await client.create_session(
                model=self.model,
                session_id=f"instabrain-{uuid.uuid4()}",
                system_message={"mode": "replace", "content": system},
                available_tools=[],
                tools=[],
                on_permission_request=_deny_all,
                enable_skills=False,
                skip_custom_instructions=True,
                enable_config_discovery=False,
                infinite_sessions={"enabled": False},
            )
            event = await asyncio.wait_for(session.send_and_wait(prompt), self._timeout)
        except TimeoutError as exc:
            raise ProviderError(f"Copilot request timed out after {self._timeout}s") from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Copilot request failed: {exc}") from exc
        finally:
            if session is not None:
                with contextlib.suppress(Exception):
                    await session.disconnect()
        text = getattr(getattr(event, "data", None), "content", None) if event else None
        if not text:
            raise ProviderError("Copilot returned an empty response")
        return Completion(text=text, provider=self.name, model=self.model)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.stop()
            self._client = None
