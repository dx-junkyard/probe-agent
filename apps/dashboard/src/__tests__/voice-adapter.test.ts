import { afterEach, describe, expect, test } from "vitest";
import { voicePrerequisite } from "@/lib/voice-adapter";

describe("voicePrerequisite", () => {
  const originalRecognition = (window as typeof window & { SpeechRecognition?: unknown }).SpeechRecognition;
  const originalSynthesis = window.speechSynthesis;

  afterEach(() => {
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: originalRecognition,
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: originalSynthesis,
    });
  });

  test("requires both speech recognition and speech synthesis", () => {
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: class FakeRecognition {},
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: undefined,
    });
    expect(voicePrerequisite()).toBe("unsupported");

    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: undefined,
    });
    Object.defineProperty(window, "speechSynthesis", {
      configurable: true,
      value: { cancel() {}, speak() {} },
    });
    expect(voicePrerequisite()).toBe("unsupported");
  });
});
