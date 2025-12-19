import * as React from "react";
import { Suspense } from "react";
import { Button, Spinner } from "@fluentui/react-components";
import { bundleIcon, DeleteFilled, DeleteRegular } from "@fluentui/react-icons";
import { CopilotMessageV2 as CopilotMessage } from "@fluentui-copilot/react-copilot-chat";
import {
  ReferenceListV2 as ReferenceList,
  ReferenceOverflowButton,
} from "@fluentui-copilot/react-reference";

import { Markdown } from "../core/Markdown";
import { UsageInfo } from "./UsageInfo";
import { IAssistantMessageProps } from "./chatbot/types";

import styles from "./AgentPreviewChatBot.module.css";
import { AgentIcon } from "./AgentIcon";

const DeleteIcon = bundleIcon(DeleteFilled, DeleteRegular);

export function AssistantMessage({
  message,
  agentLogo,
  loadingState,
  agentName,
  showUsageInfo,
  onDelete,
}: IAssistantMessageProps): React.JSX.Element {
  /* 
   * Thinking Process State Management
   * - Default to open/expanded if currently loading/streaming.
   * - Default to closed/collapsed if static/history.
   */
  const [isThinkingOpen, setIsThinkingOpen] = React.useState(
    loadingState === "loading" || loadingState === "streaming"
  );

  // Effect to auto-open when streaming starts, auto-close when done
  React.useEffect(() => {
    if (loadingState === "loading" || loadingState === "streaming") {
      setIsThinkingOpen(true);
    } else if (loadingState === "none") {
      setIsThinkingOpen(false);
    }
  }, [loadingState]);

  const hasAnnotations = message.annotations && message.annotations.length > 0;
  const references = hasAnnotations
    ? message.annotations?.map((annotation, index) => (
      <div key={index} className="reference-item">
        {annotation.text || annotation.file_name}
      </div>
    ))
    : [];

  return (
    <CopilotMessage
      id={"msg-" + message.id}
      key={message.id}
      actions={
        <span>
          {onDelete && message.usageInfo && (
            <Button
              appearance="subtle"
              icon={<DeleteIcon />}
              onClick={() => {
                void onDelete(message.id);
              }}
            />
          )}
        </span>
      }
      avatar={<AgentIcon alt="" iconName={agentLogo} />}
      className={styles.copilotChatMessage}
      disclaimer={<span>AI-generated content may be incorrect</span>}
      footnote={
        <>
          {hasAnnotations && (
            <ReferenceList
              maxVisibleReferences={3}
              minVisibleReferences={2}
              showLessButton={
                <ReferenceOverflowButton>Show Less</ReferenceOverflowButton>
              }
              showMoreButton={
                <ReferenceOverflowButton
                  text={(overflowCount) => `+${overflowCount.toString()}`}
                />
              }
            >
              {references}
            </ReferenceList>
          )}
          {showUsageInfo && message.usageInfo && (
            <UsageInfo info={message.usageInfo} duration={message.duration} />
          )}
        </>
      }
      loadingState={loadingState}
      name={agentName ?? "Bot"}
    >
      {message.thoughts && message.thoughts.length > 0 && (
        <div className={styles.thoughtContainer}>
          <div
            className={styles.thoughtSummary}
            onClick={() => setIsThinkingOpen(!isThinkingOpen)}
            style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '8px', userSelect: 'none' }}
          >
            <span style={{ fontSize: '10px', transform: isThinkingOpen ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}>▶</span>
            <span>Thinking Process ({message.thoughts.length})</span>
          </div>

          {isThinkingOpen && (
            <div className={styles.thoughtContent}>
              {message.thoughts.map((thought, idx) => (
                <div key={idx} className={styles.thoughtItem}>
                  <Markdown content={thought} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <Suspense fallback={<Spinner size="small" />}>
        <Markdown content={message.content} />
      </Suspense>
    </CopilotMessage>
  );
}
