import { ReactNode, useEffect, useMemo, useState } from "react";
import {
  Body1,
  Button,
  Caption1,
  Title2,
} from "@fluentui/react-components";
import { ChatRegular, MoreHorizontalRegular } from "@fluentui/react-icons";

import { AgentIcon } from "./AgentIcon";
import { SettingsPanel } from "../core/SettingsPanel";
import { AgentPreviewChatBot } from "./AgentPreviewChatBot";
import { MenuButton } from "../core/MenuButton/MenuButton";
import { IChatItem } from "./chatbot/types";

import styles from "./AgentPreview.module.css";

interface IAgent {
  id: string;
  object: string;
  created_at: number;
  name: string;
  description?: string | null;
  model: string;
  instructions?: string;
  tools?: Array<{ type: string }>;
  top_p?: number;
  temperature?: number;
  tool_resources?: {
    file_search?: {
      vector_store_ids?: string[];
    };
    [key: string]: any;
  };
  metadata?: Record<string, any>;
  response_format?: "auto" | string;
}

interface IAgentPreviewProps {
  resourceId: string;
  agentDetails: IAgent;
}

interface ConversationSummary {
  id: string;
  title?: string | null;
  created_at?: string;
  updated_at?: string;
  last_message?: string | null;
}


export function AgentPreview({ agentDetails }: IAgentPreviewProps): ReactNode {
  const [isSettingsPanelOpen, setIsSettingsPanelOpen] = useState(false);
  const [messageList, setMessageList] = useState<IChatItem[]>([]);
  const [isResponding, setIsResponding] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);

  const handleSettingsPanelOpenChange = (isOpen: boolean) => {
    setIsSettingsPanelOpen(isOpen);
  };

  const loadMessages = async (id: string) => {
    setIsLoadingHistory(true);
    setIsResponding(false);
    try {
      const response = await fetch(`/conversations/${id}/messages`);
      if (!response.ok) {
        console.error("[ChatClient] Failed to load messages", response.status);
        return;
      }
      const data = await response.json();
      const mapped: IChatItem[] =
        data?.messages?.map((msg: any) => ({
          id: `msg-${msg.id}`,
          role: msg.role,
          content: msg.content,
          isAnswer: msg.role === "assistant",
          thoughts: msg.metadata?.thoughts,
          more: msg.created_at ? { time: msg.created_at } : undefined,
        })) ?? [];
      setMessageList(mapped);
    } catch (error) {
      console.error("[ChatClient] Failed to load messages:", error);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const fetchConversations = async (selectIfMissing = true) => {
    try {
      const response = await fetch("/conversations");
      if (!response.ok) {
        console.error("[ChatClient] Failed to load conversations", response.status);
        return;
      }
      const data = await response.json();
      const items: ConversationSummary[] = data?.conversations ?? [];
      setConversations(items);
      if (items.length > 0 && (!conversationId && selectIfMissing)) {
        const nextId = items[0].id;
        setConversationId(nextId);
        await loadMessages(nextId);
      }
      if (items.length === 0 && !conversationId) {
        await newThread();
      }
    } catch (error) {
      console.error("[ChatClient] Failed to fetch conversations:", error);
    }
  };

  useEffect(() => {
    void fetchConversations();
  }, []);

  const newThread = async (): Promise<string | null> => {
    try {
      const response = await fetch("/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        console.error("[ChatClient] Failed to create conversation", response.status);
        return null;
      }
      const data = await response.json();
      setConversationId(data.id);
      setMessageList([]);
      await fetchConversations(false);
      return data.id as string;
    } catch (error) {
      console.error("[ChatClient] Failed to start new conversation:", error);
    }
    return null;
  };

  const deleteConversation = async (id?: string | null) => {
    if (!id) return;
    try {
      const response = await fetch(`/conversations/${id}`, { method: "DELETE" });
      if (!response.ok) {
        console.error("[ChatClient] Failed to delete conversation", response.status);
        return;
      }
      setMessageList([]);
      setConversationId(null);
      await fetchConversations();
    } catch (error) {
      console.error("[ChatClient] Failed to delete conversation:", error);
    }
  };

  const onSend = async (message: string) => {
    const userMessage: IChatItem = {
      id: `user-${Date.now()}`,
      content: message,
      role: "user",
      more: { time: new Date().toISOString() },
    };

    setMessageList((prev) => [...prev, userMessage]);

    try {
      let activeConversationId = conversationId;
      if (!activeConversationId) {
        activeConversationId = await newThread();
      }
      if (!activeConversationId) {
        console.error("[ChatClient] No conversation id available.");
        return;
      }
      const postData = {
        conversation_id: activeConversationId,
        messages: [{ role: "user", content: message }],
      };

      setIsResponding(true);
      const response = await fetch("/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(postData),
        credentials: "include",
      });

      console.log(
        "[ChatClient] Response status:",
        response.status,
        response.statusText
      );

      if (!response.ok) {
        console.error(
          "[ChatClient] The server has returned an error:",
          response.status,
          response.statusText
        );
        return;
      }

      if (!response.body) {
        throw new Error(
          "ReadableStream not supported or response.body is null"
        );
      }

      handleMessages(response.body);
    } catch (error: any) {
      setIsResponding(false);
      if (error.name === "AbortError") {
        console.log("[ChatClient] Fetch request aborted by user.");
      } else {
        console.error("[ChatClient] Fetch failed:", error);
      }
    }
  };

  const handleMessages = (
    stream: ReadableStream<Uint8Array<ArrayBufferLike>>
  ) => {
    let chatItem: IChatItem | null = null;
    let accumulatedContent = "";
    let isStreaming = true;
    let buffer = "";

    // Track multi-step workflow messages
    const messageMap = new Map<string, string>();
    const messageOrder: string[] = [];

    // Create a reader for the SSE stream
    const reader = stream.getReader();
    const decoder = new TextDecoder();

    const readStream = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        // Convert the incoming Uint8Array to text
        const textChunk = decoder.decode(value, { stream: true });

        buffer += textChunk;
        let boundary = buffer.indexOf("\n");

        // We process line-by-line.
        while (boundary !== -1) {
          const chunk = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 1);

          if (chunk.startsWith("data: ")) {
            const jsonStr = chunk.slice(6);
            let data;
            try {
              data = JSON.parse(jsonStr);
            } catch (err) {
              console.error("[ChatClient] Failed to parse JSON:", jsonStr, err);
              boundary = buffer.indexOf("\n");
              continue;
            }

            if (data.type === "conversation" && data.conversation_id) {
              setConversationId(data.conversation_id);
            }

            if (data.error) {
              if (!chatItem) {
                chatItem = createAssistantMessageDiv();
              }

              setIsResponding(false);
              appendAssistantMessage(
                chatItem,
                data.error.message || "An error occurred.",
                false
              );
              return;
            }

            if (data.type === "stream_end") {
              setIsResponding(false);
              void fetchConversations(false);
              break;
            } else {
              if (!chatItem) {
                chatItem = createAssistantMessageDiv();
              }

              let thoughts: string[] = [];
              if (messageOrder.length > 0) {
                thoughts = messageOrder.slice(0, -1).map(mid => messageMap.get(mid) || "");
              }

              if (data.type === "completed_message") {
                clearAssistantMessage(chatItem);
                accumulatedContent = data.content;
                isStreaming = false;

                setIsResponding(false);
                void fetchConversations(false);

                appendAssistantMessage(chatItem, accumulatedContent, isStreaming, thoughts);
              } else if (data.type === "message_delta") {
                // New logic for handling structured thoughts + answer
                const id = data.id || "default";
                if (!messageMap.has(id)) {
                  messageMap.set(id, "");
                  messageOrder.push(id);
                }
                const current = messageMap.get(id) || "";
                messageMap.set(id, current + data.content);

                // Determine thoughts vs answer (last one is answer)
                if (messageOrder.length > 0) {
                  thoughts = messageOrder.slice(0, -1).map(mid => messageMap.get(mid) || "");
                  const answerId = messageOrder[messageOrder.length - 1];
                  accumulatedContent = messageMap.get(answerId) || "";
                }

                appendAssistantMessage(chatItem, accumulatedContent, isStreaming, thoughts);
              } else {
                // Fallback for standard messages
                accumulatedContent += data.content;
                appendAssistantMessage(chatItem, accumulatedContent, isStreaming, thoughts);
              }
            }
          }

          boundary = buffer.indexOf("\n");
        }
      }
    };

    // Catch errors from the stream reading process
    readStream().catch((error) => {
      console.error("[ChatClient] Stream reading failed:", error);
      setIsResponding(false);
    });
  };

  const createAssistantMessageDiv: () => IChatItem = () => {
    const item = { id: crypto.randomUUID(), content: "", isAnswer: true, more: { time: new Date().toISOString() } };
    setMessageList((prev) => [...prev, item]);
    return item;
  };
  const appendAssistantMessage = (
    chatItem: IChatItem,
    accumulatedContent: string,
    isStreaming: boolean,
    thoughts: string[] = []
  ) => {
    try {
      if (!chatItem) {
        throw new Error("Message content div not found in the template.");
      }

      chatItem.content = accumulatedContent;
      chatItem.thoughts = thoughts;

      setMessageList((prev) => {
        return [...prev.slice(0, -1), { ...chatItem }];
      });

      if (!isStreaming) {
        requestAnimationFrame(() => {
          const lastChild = document.getElementById(`msg-${chatItem.id}`);
          if (lastChild) {
            lastChild.scrollIntoView({ behavior: "smooth", block: "end" });
          }
        });
      }
    } catch (error) {
      console.error("Error in appendAssistantMessage:", error);
    }
  };

  const clearAssistantMessage = (chatItem: IChatItem) => {
    if (chatItem) {
      chatItem.content = "";
    }
  };
  const menuItems = [
    {
      key: "settings",
      children: "Settings",
      onClick: () => {
        setIsSettingsPanelOpen(true);
      },
    },
    {
      key: "terms",
      children: (
        <a
          className={styles.externalLink}
          href="https://aka.ms/aistudio/terms"
          target="_blank"
          rel="noopener noreferrer"
        >
          Terms of Use
        </a>
      ),
    },
    {
      key: "privacy",
      children: (
        <a
          className={styles.externalLink}
          href="https://go.microsoft.com/fwlink/?linkid=521839"
          target="_blank"
          rel="noopener noreferrer"
        >
          Privacy
        </a>
      ),
    },
    {
      key: "feedback",
      children: "Send Feedback",
      onClick: () => {
        alert("Thank you for your feedback!");
      },
    },
    {
      key: "delete",
      children: "Delete Chat",
      onClick: () => {
        void deleteConversation(conversationId);
      },
    },
  ];

  const chatContext = useMemo(
    () => ({
      messageList,
      isResponding: isResponding || isLoadingHistory,
      onSend,
    }),
    [messageList, isResponding, isLoadingHistory]
  );

  const handleConversationSelect = async (id: string) => {
    setConversationId(id);
    await loadMessages(id);
  };

  return (
    <div className={styles.container}>
      <div className={styles.topBar}>
        <div className={styles.leftSection}>
          <select
            className={styles.conversationSelect}
            value={conversationId ?? ""}
            onChange={(e) => {
              const id = e.target.value;
              if (id) {
                void handleConversationSelect(id);
              }
            }}
          >
            <option value="" disabled>
              Select chat
            </option>
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title || `Chat ${c.id.slice(0, 8)}`}
              </option>
            ))}
          </select>
          {messageList.length > 0 && (
            <>
              <AgentIcon
                alt=""
                iconClassName={styles.agentIcon}
                iconName={agentDetails.metadata?.logo}
              />
              <Body1 className={styles.agentName}>{agentDetails.name}</Body1>
            </>
          )}
        </div>
        <div className={styles.rightSection}>
          <Button
            appearance="subtle"
            icon={<ChatRegular aria-hidden={true} />}
            onClick={() => void newThread()}
          >
            New Chat
          </Button>
          <Button
            appearance="subtle"
            disabled={!conversationId}
            onClick={() => void deleteConversation(conversationId)}
          >
            Delete
          </Button>
          <MenuButton
            menuButtonText=""
            menuItems={menuItems}
            menuButtonProps={{
              appearance: "subtle",
              icon: <MoreHorizontalRegular />,
              "aria-label": "Settings",
            }}
          />
        </div>
      </div>
      <div className={styles.content}>
        <>
          {messageList.length === 0 && (
            <div className={styles.emptyChatContainer}>
              <AgentIcon
                alt=""
                iconClassName={styles.emptyStateAgentIcon}
                iconName={agentDetails.metadata?.logo}
              />
              <Caption1 className={styles.agentName}>
                {agentDetails.name}
              </Caption1>
              <Title2>How can I help you today?</Title2>
            </div>
          )}
          <AgentPreviewChatBot
            agentName={agentDetails.name}
            agentLogo={agentDetails.metadata?.logo}
            chatContext={chatContext}
          />
        </>
      </div>

      {/* Settings Panel */}
      <SettingsPanel
        isOpen={isSettingsPanelOpen}
        onOpenChange={handleSettingsPanelOpenChange}
      />
    </div>
  );
}
