export const OPEN_ASSISTANT_EVENT = "probe-agent:open-assistant";

export type OpenAssistantDetail = {
  question?: string;
};

/** Open the screen assistant without submitting anything on the user's behalf. */
export function openScreenAssistant(question?: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<OpenAssistantDetail>(OPEN_ASSISTANT_EVENT, {
      detail: { question },
    }),
  );
}
