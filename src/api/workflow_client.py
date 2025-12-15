from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

import aiohttp
from azure.core.credentials import TokenCredential

from .util import get_logger

logger = get_logger("azureaiapp_workflow_client")


class WorkflowInvocationError(RuntimeError):
    """Raised when the Foundry published application returns a non-success response."""


class WorkflowClient:
    """
    Client for invoking a published Foundry Agent Application via the OpenAI-compatible
    Responses endpoint.

    Agent Applications only support POST /responses; other OpenAI surfaces like /conversations
    are not available, so clients must store multi-turn history themselves.
    """

    _SCOPE = "https://ai.azure.com/.default"

    def __init__(self, endpoint: str, credential: TokenCredential):
        """
        :param endpoint: Full URL to the agent application's responses endpoint, including api-version.
                         Example:
                           https://{account}.services.ai.azure.com/api/projects/{proj}/applications/{app}/protocols/openai/responses?api-version=2025-11-15-preview
        :param credential: Azure TokenCredential (AzureDeveloperCliCredential, ManagedIdentityCredential, etc.)
        """
        if "api-version=" not in endpoint:
            raise ValueError(f"Endpoint must include api-version query param, got: {endpoint}")

        self.endpoint = endpoint
        self.credential = credential

        logger.info("WorkflowClient loaded from: %s", __file__)  # <-- confirms which file is running

    def _get_token(self) -> str:
        """
        Acquire a bearer token for the Foundry agent application endpoint.
        """
        token = self.credential.get_token(self._SCOPE)
        return token.token



    async def stream_conversation(
        self,
        messages: list[dict],
        conversation_id: Optional[str] = None,  # intentionally NOT sent to the agent endpoint
    ) -> AsyncGenerator[str, None]:
        """
        Call the agent application and yield frontend-friendly JSON strings:
          {"type":"message","content":"...delta..."}
          {"type":"completed_message","content":"...final..."}
        """
        token = self._get_token()
        token = self._get_token()

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        if "/protocols/activityprotocol" in self.endpoint:
            payload = {
                "type": "message",
                "text": messages[-1]["content"] if messages else "",
                "from": {"id": "user", "name": "User"},
                "recipient": {"id": "bot", "name": "Bot"},
                "locale": "en-US",
                "conversation": {"id": conversation_id or "default"},
                "channelId": "directline"
            }
        else:
            payload = {
                "messages": messages,
                "stream": True,
                "conversation": {
                    "id": conversation_id
                }
            }

        # 🔎 THE MOST IMPORTANT DEBUG LINE:
        logger.info("Posting to %s", self.endpoint)
        logger.info("Payload keys=%s payload=%s", list(payload.keys()), json.dumps(payload)[:1000])

        async with aiohttp.ClientSession() as session:
            async with session.post(self.endpoint, headers=headers, json=payload) as resp:
                if resp.status not in (200, 202):
                    body_text = await resp.text()
                    raise WorkflowInvocationError(
                        f"Workflow returned HTTP {resp.status}. Body: {body_text}"
                    )
                
                if resp.status == 202:
                    logger.info("Workflow accepted the request (Async). No immediate response content.")
                    yield json.dumps({"type": "message", "content": "[Message sent to async endpoint. No response received.]"})
                    yield json.dumps({"type": "completed_message", "content": "[Message sent to async endpoint. No response received.]"})
                    return

                content_type = resp.headers.get("Content-Type", "")

                # Minimal streaming handling (SSE "data:" lines)
                if "text/event-stream" in content_type:
                    accumulated: list[str] = []

                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue

                        data_str = line[len("data:") :].strip()
                        if data_str == "[DONE]":
                            break

                        evt = json.loads(data_str)
                        if evt.get("type") == "response.output_text.delta":
                            delta = evt.get("delta")
                            if isinstance(delta, str) and delta:
                                accumulated.append(delta)
                                yield json.dumps({"type": "message", "content": delta})

                        if evt.get("type") == "response.completed":
                            # Some implementations include full response here; fallback to deltas if not.
                            final = "".join(accumulated)
                            yield json.dumps({"type": "completed_message", "content": final})
                            return

                    final = "".join(accumulated)
                    yield json.dumps({"type": "completed_message", "content": final})
                    return

                # Non-streaming JSON fallback
                response_json = await resp.json()
                text = response_json.get("output_text") or json.dumps(response_json)
                yield json.dumps({"type": "message", "content": text})
                yield json.dumps({"type": "completed_message", "content": text})
