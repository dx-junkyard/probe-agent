import json

from app.voice_speech import (
    DEFAULT_SPEECH_INSTRUCTIONS,
    DETAIL_OFFER,
    SpeechConfig,
    project_spoken_answer,
    spoken_summary,
    stream_speech,
)


def test_spoken_summary_strips_visual_markup_and_pauses_before_details():
    answer = (
        "## 全体像\n"
        "- 最初の結論です。\n"
        "- [設定画面](https://example.invalid/settings)を確認します。\n"
        "- 三つ目の要点です。\n"
        "- 読み上げない詳細です。"
    )
    result = spoken_summary(answer)
    assert result is not None
    assert "#" not in result
    assert "https://" not in result
    assert "設定画面" in result
    assert "読み上げない詳細" not in result
    assert result.endswith(DETAIL_OFFER)


def test_spoken_summary_keeps_a_short_complete_answer_as_is():
    assert spoken_summary("結論は一つです。") == "結論は一つです。"
    assert project_spoken_answer("結論は一つです。").has_more is False


def test_empty_instruction_env_uses_the_natural_japanese_default(monkeypatch):
    monkeypatch.setenv("OPENAI_TTS_INSTRUCTIONS", "")
    assert SpeechConfig.from_env().instructions == DEFAULT_SPEECH_INSTRUCTIONS


def test_stream_speech_calls_openai_audio_speech(monkeypatch):
    captured = {}

    class FakeResponse:
        def __init__(self):
            self.parts = iter((b"abc", b"def", b""))
            self.closed = False

        def read(self, _size):
            return next(self.parts)

        def close(self):
            self.closed = True

    response = FakeResponse()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("app.voice_speech.urllib.request.urlopen", fake_urlopen)
    config = SpeechConfig(
        api_key="secret",
        model="gpt-4o-mini-tts",
        voice="marin",
        instructions="calm",
        base_url="https://api.openai.com/v1",
        timeout=12,
    )
    assert b"".join(stream_speech("要点です。", config)) == b"abcdef"
    assert captured["url"] == "https://api.openai.com/v1/audio/speech"
    assert captured["body"] == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": "要点です。",
        "instructions": "calm",
        "response_format": "mp3",
    }
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 12
    assert response.closed is True
