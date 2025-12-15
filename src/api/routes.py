# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license.
# See LICENSE file in the project root for full license information.
import json
import logging
import os
import secrets
from typing import Dict, List, Optional

import fastapi
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from azure.ai.inference.prompts import PromptTemplate
from azure.ai.inference.aio import ChatCompletionsClient
from pydantic import BaseModel

from .chat_store import ChatStore
from .search_index_manager import SearchIndexManager
from .util import ChatRequest, get_logger
from azure.core.exceptions import HttpResponseError


from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()


username = os.getenv("WEB_APP_USERNAME")
password = os.getenv("WEB_APP_PASSWORD")
basic_auth = username and password

def authenticate(credentials: Optional[HTTPBasicCredentials] = Depends(security)) -> None:

    if not basic_auth:
        logger.info("Skipping authentication: WEB_APP_USERNAME or WEB_APP_PASSWORD not set.")
        return
    
    correct_username = secrets.compare_digest(credentials.username, username)
    correct_password = secrets.compare_digest(credentials.password, password)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return

auth_dependency = Depends(authenticate) if basic_auth else None

logger = get_logger(
    name="azureaiapp_routes",
    log_level=logging.INFO,
    log_file_name=os.getenv("APP_LOG_FILE"),
    log_to_console=True
)

router = fastapi.APIRouter()
templates = Jinja2Templates(directory="api/templates")


# Accessors to get app state
def get_chat_store(request: Request) -> ChatStore:
    return request.app.state.chat_store


def get_chat_client(request: Request) -> ChatCompletionsClient:
    return request.app.state.chat


def get_chat_model(request: Request) -> str:
    return request.app.state.chat_model


def get_search_index_namager(request: Request) -> SearchIndexManager:
    return request.app.state.search_index_manager

def get_workflow_client(request: Request):
    return getattr(request.app.state, "workflow_client", None)


class ConversationCreate(BaseModel):
    title: Optional[str] = None


class ConversationResponse(BaseModel):
    id: str
    title: Optional[str]
    created_at: str
    updated_at: str
    last_message: Optional[str] = None


class ConversationsResponse(BaseModel):
    conversations: List[ConversationResponse]


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: str
    metadata: Optional[dict] = None


class MessagesResponse(BaseModel):
    messages: List[MessageResponse]

def serialize_sse_event(data: Dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.get("/", response_class=HTMLResponse)
async def index_name(request: Request, _ = auth_dependency):
    # Read the manifest file to get the hashed filenames
    manifest_path = "api/static/react/.vite/manifest.json"
    js_url = "/static/react/assets/main-react-app.js" # Fallback
    css_url = "/static/react/assets/main-react-app.css" # Fallback

    if os.path.exists(manifest_path):
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
            if "src/main.tsx" in manifest:
                js_file = manifest["src/main.tsx"]["file"]
                js_url = f"/static/react/{js_file}"
            
            # Simple heuristic: find the first CSS file in the manifest or linked from main
            # Based on observed manifest, style.css is a top level key
            if "style.css" in manifest:
                css_file = manifest["style.css"]["file"]
                css_url = f"/static/react/{css_file}"
            # Also check if it's nested under the entry (standard vite behavior sometimes)
            elif "src/main.tsx" in manifest and "css" in manifest["src/main.tsx"]:
                 css_file = manifest["src/main.tsx"]["css"][0]
                 css_url = f"/static/react/{css_file}"

    return templates.TemplateResponse(
        "index.html", 
        {
            "request": request,
            "js_url": js_url,
            "css_url": css_url,
        }
    )


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    conversation: ConversationCreate | None = None,
    chat_store: ChatStore = Depends(get_chat_store),
    _ = auth_dependency,
):
    conversation_row = chat_store.create_conversation(conversation.title if conversation else None)
    return {**conversation_row, "last_message": None}


@router.get("/conversations", response_model=ConversationsResponse)
async def list_conversations(
    limit: int = 20,
    offset: int = 0,
    chat_store: ChatStore = Depends(get_chat_store),
    _ = auth_dependency,
):
    conversations = chat_store.list_conversations(limit=limit, offset=offset)
    return {"conversations": conversations}


@router.get("/conversations/{conversation_id}/messages", response_model=MessagesResponse)
async def list_messages(
    conversation_id: str,
    limit: int = 200,
    offset: int = 0,
    chat_store: ChatStore = Depends(get_chat_store),
    _ = auth_dependency,
):
    if not chat_store.conversation_exists(conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    messages = chat_store.get_messages(conversation_id, limit=limit, offset=offset)
    return {"messages": messages}


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    chat_store: ChatStore = Depends(get_chat_store),
    _ = auth_dependency,
):
    if not chat_store.conversation_exists(conversation_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    chat_store.delete_conversation(conversation_id)
    return fastapi.Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat")
async def chat_stream_handler(
    chat_request: ChatRequest,
    chat_client: ChatCompletionsClient = Depends(get_chat_client),
    model_deployment_name: str = Depends(get_chat_model),
    search_index_manager: SearchIndexManager = Depends(get_search_index_namager),
    chat_store: ChatStore = Depends(get_chat_store),
    workflow_client = Depends(get_workflow_client),
    _ = auth_dependency
) -> fastapi.responses.StreamingResponse:
    
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream"
    }    
    
    # If the workflow client is enabled, we bypass the standard chat logic
    if workflow_client:
        # We can still validate/create conversation
        if chat_request.conversation_id:
            if not chat_store.conversation_exists(chat_request.conversation_id):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
            conversation_id = chat_request.conversation_id
        else:
            conversation = chat_store.create_conversation()
            conversation_id = conversation["id"]

        async def workflow_stream():
            yield serialize_sse_event({"type": "conversation", "conversation_id": conversation_id})
            
            # Persist user message
            incoming_messages = chat_request.messages or []
            for msg in incoming_messages:
                chat_store.append_message(conversation_id, msg.role, msg.content)

            # Get full history (WorkflowClient might want it, or we just send latest)
            # Our WorkflowClient logic sends the latest user message.
            history_messages = [
                {"role": message["role"], "content": message["content"]}
                for message in chat_store.get_messages(conversation_id)
            ]

            try:
                accumulated_response = ""
                async for chunk in workflow_client.stream_conversation(history_messages, conversation_id):
                    # The workflow client yields pre-formatted data: lines. 
                    # But wait, our client yields raw data content strings or jsons? 
                    # Let's check WorkflowClient.stream_conversation:
                    # It calls `yield decoded_line[5:].strip()` for SSE 
                    # OR `yield json.dumps({...})` for non-streaming.
                    # But serialize_sse_event expects a Dict.
                    
                    # WorkflowClient yields STRINGS (json string content of the data: field).
                    # But serialize_sse_event takes a Dict. 
                    
                    # Let's verify WorkflowClient output.
                    # It yields `decoded_line[5:].strip()` which is the JSON payload of the SSE event.
                    
                    try:
                        data = json.loads(chunk)
                        
                        # Capture content for persistence
                        if data.get("type") in ["message", "completed_message"] and "content" in data:
                            # Avoid double counting if we get both message and completed_message with same content
                            # But typical SSE from standard chat sends deltas, then completed.
                            # Standard AI Foundry usually sends "content" as delta.
                            # If it's a delta:
                            if data.get("type") == "message":
                                accumulated_response += data["content"]
                        
                        yield serialize_sse_event(data)
                    except json.JSONDecodeError:
                        # Fallback if raw string
                        logger.warning(f"Could not parse workflow chunk: {chunk}")
                        pass
                
                # Persist assistant response
                if accumulated_response:
                    chat_store.append_message(conversation_id, "assistant", accumulated_response)
                    
                yield serialize_sse_event({"type": "stream_end"})

            except Exception as e:
                logger.error(f"Workflow error: {e}")
                yield serialize_sse_event({
                    "content": f"Error calling workflow: {str(e)}",
                    "type": "completed_message"
                })
                yield serialize_sse_event({"type": "stream_end"})

        return StreamingResponse(workflow_stream(), headers=headers)


    if chat_client is None:
        raise Exception("Chat client not initialized")

    if chat_request.conversation_id:
        if not chat_store.conversation_exists(chat_request.conversation_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
        conversation_id = chat_request.conversation_id
    else:
        conversation = chat_store.create_conversation()
        conversation_id = conversation["id"]

    async def response_stream():
        yield serialize_sse_event({"type": "conversation", "conversation_id": conversation_id})

        # Persist incoming user messages and build full history for the model
        incoming_messages = chat_request.messages or []
        for msg in incoming_messages:
            chat_store.append_message(conversation_id, msg.role, msg.content)

        history_messages = [
            {"role": message["role"], "content": message["content"]}
            for message in chat_store.get_messages(conversation_id)
        ]

        prompt_messages = PromptTemplate.from_string('You are a helpful assistant').create_messages()
        # Use RAG model, only if we were provided index and we have found a context there.
        if search_index_manager is not None:
            context = await search_index_manager.search(chat_request)
            if context:
                prompt_messages = PromptTemplate.from_string(
                    'You are a helpful assistant that answers some questions '
                    'with the help of some context data.\n\nHere is '
                    'the context data:\n\n{{context}}').create_messages(data=dict(context=context))
                logger.info(f"{prompt_messages=}")
            else:
                logger.info("Unable to find the relevant information in the index for the request.")
        try:
            accumulated_message = ""
            chat_coroutine = await chat_client.complete(
                model=model_deployment_name, messages=prompt_messages + history_messages, stream=True
            )
            async for event in chat_coroutine:
                if event.choices:
                    first_choice = event.choices[0]
                    if first_choice.delta.content:
                        message = first_choice.delta.content
                        accumulated_message += message
                        yield serialize_sse_event({
                                        "content": message,
                                        "type": "message",
                                    }
                                )

            yield serialize_sse_event({
                "content": accumulated_message,
                "type": "completed_message",
            })                        
            if accumulated_message:
                chat_store.append_message(conversation_id, "assistant", accumulated_message)
        except BaseException as e:
            error_processed = False
            response = "There is an error!"
            try:
                if '(content_filter)' in e.args[0]:
                    rai_dict = e.response.json()['error']['innererror']['content_filter_result']
                    errors = []
                    for k, v in rai_dict.items():
                        if v['filtered']:
                            if 'severity' in v:
                                errors.append(f"{k}, severity: {v['severity']}")
                            else:
                                errors.append(k)
                    error_text = f"We have found the next safety issues in the response: {', '.join(errors)}"
                    logger.error(error_text)
                    response = error_text
                    error_processed = True
            except BaseException:
                pass
            if not error_processed:
                error_text = str(e)
                logger.error(error_text)
                response = error_text
            yield serialize_sse_event({
                            "content": response,
                            "type": "completed_message",
                        })
        yield serialize_sse_event({
            "type": "stream_end"
            })

    return StreamingResponse(response_stream(), headers=headers)
