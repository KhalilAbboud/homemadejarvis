from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import gradio as gr
from faster_whisper import WhisperModel

from brain import LLMError, stream_llm_response
from tts import TTSError, speak

EXIT_PHRASES = {"exit", "quit", "goodbye", "stop", "bye"}


async def get_osiris_reply(user_text: str) -> str:
    """Return a completed response from the existing OSIRIS backend."""
    reply_parts: list[str] = []
    try:
        async for token in stream_llm_response(user_text):
            reply_parts.append(token)
    except LLMError:
        raise
    return "".join(reply_parts).strip() or "I don't have a response right now."


async def text_chat(message: str, history: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not message or not message.strip():
        return history

    normalized = message.strip().lower()
    if normalized in EXIT_PHRASES:
        return history + [(message, "Goodbye.")]

    try:
        reply = await get_osiris_reply(message)
    except LLMError as exc:
        reply = f"Error: {exc}"
    return history + [(message, reply)]


def transcribe_audio_file(audio_path: str | None) -> str:
    if not audio_path:
        return ""

    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, beam_size=5)
    transcript = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
    return transcript.strip()


async def voice_submit(audio) -> tuple[str, str]:
    if audio is None:
        return "", "No audio captured."

    audio_path = audio["name"] if isinstance(audio, dict) else str(audio)
    if not audio_path or not Path(audio_path).exists():
        # Gradio often passes a dict with a path property when using microphone capture.
        if isinstance(audio, dict):
            audio_path = audio.get("path")

    if not audio_path or not Path(audio_path).exists():
        return "", "No valid audio file was provided."

    try:
        transcript = await asyncio.to_thread(transcribe_audio_file, audio_path)
    except Exception as exc:  # pragma: no cover - surfaced to UI
        return "", f"Audio transcription failed: {exc}"

    if not transcript:
        return "", "I couldn't hear anything clearly in that recording."

    if transcript.strip().lower() in EXIT_PHRASES:
        try:
            await speak("Goodbye.")
        except TTSError:
            pass
        return transcript, "Goodbye."

    try:
        reply = await get_osiris_reply(transcript)
    except LLMError as exc:
        reply = f"Error: {exc}"

    try:
        await speak(reply)
    except TTSError:
        pass

    return transcript, reply


with gr.Blocks(title="OSIRIS") as demo:
    gr.Markdown("# OSIRIS Assistant\nModern chat and voice interface powered by the existing backend.")

    with gr.Tab("Text mode"):
        chatbot = gr.Chatbot(height=500)
        with gr.Row():
            msg = gr.Textbox(
                placeholder="Ask OSIRIS anything...",
                scale=8,
                container=False,
            )
            send = gr.Button("Send", variant="primary")

        send.click(text_chat, inputs=[msg, chatbot], outputs=[chatbot])
        msg.submit(text_chat, inputs=[msg, chatbot], outputs=[chatbot])

    with gr.Tab("Voice mode"):
        mic = gr.Audio(sources=["microphone"], type="filepath", label="Record your voice")
        transcript_box = gr.Textbox(label="Transcript", lines=3)
        response_box = gr.Textbox(label="OSIRIS reply", lines=6)
        mic.change(fn=voice_submit, inputs=[mic], outputs=[transcript_box, response_box])

    gr.Markdown("Use the text tab for quick chat, or the voice tab to speak naturally and hear a reply back.")

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        debug=False,
        share=False,
        theme=gr.themes.Soft(),
    )
