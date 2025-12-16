from __future__ import annotations

import inspect
import json
import logging
from typing import AsyncGenerator, Optional

from azure.ai.projects.aio import AIProjectClient

from .util import get_logger

logger = get_logger("azureaiapp_workflow_client", log_level=logging.INFO)


class WorkflowInvocationError(RuntimeError):
    """Raised when the Foundry published application returns a non-success response."""


class WorkflowClient:
    """
    Client for invoking a published Foundry Agent Application via the azure-ai-projects SDK.

    Notes:
      - We intentionally avoid importing ResponseStreamEventType because some azure-ai-projects
        versions don't export it from azure.ai.projects.models. Instead, we compare to the
        documented string event types (e.g. "response.output_text.delta"). :contentReference[oaicite:4]{index=4}
      - For the async AIProjectClient, get_openai_client() returns an AsyncOpenAI directly
        (no await). :contentReference[oaicite:5]{index=5}
    """

    # Documented event type strings (subset we care about)
    _EV_OUTPUT_TEXT_DELTA = "response.output_text.delta"
    _EV_OUTPUT_TEXT_DONE = "response.output_text.done"
    _EV_OUTPUT_ITEM_ADDED = "response.output_item.added"
    _EV_OUTPUT_ITEM_DONE = "response.output_item.done"
    _EV_ERROR = "error"

    def __init__(self, project_client: AIProjectClient, workflow_name: str = "attempt-1"):
        """
        :param project_client: Initialized async AIProjectClient.
        :param workflow_name: Name of the workflow agent/application to invoke.
        """
        self.project_client = project_client
        self.workflow_name = workflow_name
        logger.info("WorkflowClient initialized with workflow_name=%s", self.workflow_name)

    @staticmethod
    def _event_type_str(event: object) -> str:
        """
        Normalize event.type to a string.

        Some SDKs may expose enum-like values with a .value property; others use plain strings.
        """
        t = getattr(event, "type", None)
        if hasattr(t, "value"):
            return str(getattr(t, "value"))
        return str(t)

    @staticmethod
    async def _maybe_await(value):
        """Await value if it's awaitable; otherwise return it."""
        if inspect.isawaitable(value):
            return await value
        return value

    @staticmethod
    async def _maybe_close(obj: object) -> None:
        """Call/await obj.close() if present."""
        close_fn = getattr(obj, "close", None)
        if close_fn is None:
            return
        await WorkflowClient._maybe_await(close_fn())

    def _messages_to_transcript(self, messages: list[dict]) -> str:
        """
        Convert stored chat history into a single prompt string.

        This keeps your existing behavior (transcript-style prompt).
        """
        lines: list[str] = []

        for m in messages:
            role = m.get("role")
            content = m.get("content")

            if role not in ("system", "user", "assistant"):
                continue
            if content is None:
                continue

            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)

            prefix = "System" if role == "system" else ("User" if role == "user" else "Assistant")
            lines.append(f"{prefix}: {content}")

        if not lines:
            return ""

        if not lines[-1].startswith("Assistant:"):
            lines.append("Assistant:")

        # log the transcript
        logger.info("Transcript: %s", "\n".join(lines))

        return "\n".join(lines)

    async def stream_conversation(
        self,
        messages: list[dict],
        conversation_id: Optional[str] = None,  # kept for signature compatibility
    ) -> AsyncGenerator[str, None]:
        """
        Invoke the Foundry workflow and stream deltas.

        Yields frontend-friendly JSON strings:
          {"type":"message","content":".delta."}
          {"type":"completed_message","content":".final."}
        """
        transcript = self._messages_to_transcript(messages)
        if not transcript:
            logger.warning("Empty transcript, skipping workflow invocation.")
            return

        openai_client = None
        conversation = None

        try:
            # IMPORTANT: for async AIProjectClient this is NOT awaitable. :contentReference[oaicite:6]{index=6}
            openai_client = self.project_client.get_openai_client()

            conversation = await openai_client.conversations.create()
            logger.info("Created conversation (id: %s)", conversation.id)

            stream = await openai_client.responses.create(
                conversation=conversation.id,
                extra_body={"agent": {"name": self.workflow_name, "type": "agent_reference"}},
                input=transcript,
                stream=True,
                metadata={"x-ms-debug-mode-enabled": "1"},
            )

            accumulated: list[str] = []

            async for event in stream:
                etype = self._event_type_str(event)

                if etype == self._EV_OUTPUT_TEXT_DELTA:
                    delta = getattr(event, "delta", None)
                    if delta:
                        accumulated.append(delta)
                        yield json.dumps({"type": "message", "content": delta})

                elif etype == self._EV_OUTPUT_TEXT_DONE:
                    # final text chunk done (we assemble from deltas)
                    pass

                elif etype in (self._EV_OUTPUT_ITEM_ADDED, self._EV_OUTPUT_ITEM_DONE):
                    # Optional: inspect workflow actions
                    item = getattr(event, "item", None)
                    if item is not None and getattr(item, "type", None) == "workflow_action":
                        yield json.dumps(
                            {
                                "type": "message",
                                "content": f"[workflow_action] {getattr(item, 'action_id', '')} "
                                           f"status={getattr(item, 'status', '')}",
                            }
                        )

                elif etype == self._EV_ERROR:
                    logger.error("Stream error event: %s", event)
                    yield json.dumps({"type": "message", "content": f"[Error event] {event}"})

                else:
                    # keep quiet or log if you want:
                    # logger.debug("Unhandled event type: %s", etype)
                    pass

            yield json.dumps({"type": "completed_message", "content": "".join(accumulated)})

        except Exception as e:
            logger.error("Workflow invocation failed: %s", e)
            raise WorkflowInvocationError(str(e)) from e

        finally:
            # Best-effort cleanup
            if openai_client is not None and conversation is not None:
                try:
                    await openai_client.conversations.delete(conversation_id=conversation.id)
                    logger.info("Conversation deleted")
                except Exception as e:
                    logger.warning("Failed to delete conversation: %s", e)

            if openai_client is not None:
                try:
                    await self._maybe_close(openai_client)
                except Exception:
                    # Don't crash shutdown/cleanup
                    pass
